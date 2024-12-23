import os
import time
import threading
import uuid
import getpass
from collections import defaultdict
import pyrebase
from ruuvitag_sensor.ruuvi import RuuviTagSensor, RunFlag
from datetime import datetime, timezone
import json

# Device UUID
device_uuid = None
# Time Interval
time_interval = 1

broadcasting = False

#-----------------------------------------------------------------------------#
#                                RUUVI RELATED                                #
#-----------------------------------------------------------------------------#
run_flag = RunFlag()

unique_macs = set()
filtered_macs = set()
auto_filtering = True

#-----------------------------------------------------------------------------#

#-----------------------------------------------------------------------------#
#                              FIREBASE RELATED                               #
#-----------------------------------------------------------------------------#
user_uid = None
firebaseConfig = None
firebase = None
auth = None
db = None

last_sent_timestamp = None 
time_stamps = defaultdict(lambda: None)
data_history = list()

id_token = ""
refresh_token = ""
token_expiration_time = 0
refresh_token_interval = 0
token_update_time = 1800    # 1800 seconds = 30 minutes


#-----------------------------------------------------------------------------#

#-----------------------------------------------------------------------------#
#                                 UI RELATED                                  #
#-----------------------------------------------------------------------------#

ui_buffer = ""

# Unicode codes:
# 2500: ─, 2502: │, 250C: ┌,  2510: ┐, 2514: └, 2518: ┘, 251C: ├, 2524: ┤

#------------------------------------------------------------------------------
# ui_clear - Clears the screen
#------------------------------------------------------------------------------
def ui_clear():
    global ui_buffer
    ui_buffer = ""
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\033[H\033[J", end="")

#------------------------------------------------------------------------------
# ui_title - Prints a title
#------------------------------------------------------------------------------
def ui_title(text: str, width: int):
    global ui_buffer

    output = "\u250C" + "\u2500" * width + "\u2510\n"
    output += "\u2502" + text.center(width) + "\u2502\n"
    output += "\u2514" + "\u2500" * width + "\u2518\n"

    ui_buffer += output

#------------------------------------------------------------------------------
# ui_table - Prints a table
#------------------------------------------------------------------------------
def ui_table(title: str, content: list, width: int):
    global ui_buffer

    # Title
    output = "\u250C" + "\u2500" * width + "\u2510\n"
    output += "\u2502" +  "\u0020" +  title + "\u0020" * (width - len(title)- 1) + "\u2502\n"
    output += "\u251C" + "\u2500" * width + "\u2524\n"
    # Content
    for e in content:
        output += "\u2502" + "\u0020" + e + "\u0020" * (width - len(e) - 1) + "\u2502\n"
    output += "\u2514" + "\u2500" * width + "\u2518\n"

    ui_buffer += output

#------------------------------------------------------------------------------
# ui_commands - Prints commands
#------------------------------------------------------------------------------
def ui_commands(commands: list):
    global ui_buffer
    output = ""

    for i in range(len(commands)):
        output +=f" {i + 1}: {commands[i]}\n" 

    ui_buffer += output

#------------------------------------------------------------------------------
# ui_enter_command - Handles commands
#------------------------------------------------------------------------------
def ui_enter_command(text: str, commands: list):
    global ui_buffer

    ui_buffer += "\n" + text
    print(ui_buffer, end="", flush=True)
    command = input()

    if command.isdigit():
        command = int(command)

        if command > 0 and command <= len(commands):
            commands[command - 1]()



#-----------------------------------------------------------------------------#
#                                   SCREENS                                   #
#-----------------------------------------------------------------------------#

#------------------------------------------------------------------------------
# broadcast_screen - Broadcast Screen
#------------------------------------------------------------------------------
def broadcast_screen():
    global broadcasting

    settings_path = "./settings.txt"

    if not broadcasting:
        try:
            with open(settings_path, 'w') as f:
                f.write(f"{user_uid}\n")
                f.write(f"{refresh_token}\n")   
        except IOError:
            print("settings.txt: Could not create or write file")
            exit()

        broadcasting = True

    # refresh_user_token - Refreshes the user's token -------------------------
    def refresh_user_token():
        global user_uid, id_token, refresh_token, token_expiration_time

        try:
            tokens = auth.refresh(refresh_token)
            id_token = tokens['idToken']
            refresh_token = tokens['refreshToken']
            token_expiration_time = int(time.time()) + 3600 # 3600 seconds = 1 hour
        except Exception as e:
            print("Error refreshing token:", e)

        try:
            with open(settings_path, 'w') as f:
                f.write(f"{user_uid}\n")
                f.write(f"{refresh_token}\n")
        except IOError:
            print("settings.txt: Could not create or write file")
            exit()
    #--------------------------------------------------------------------------

    # send_sensors - Callback function for sending Ruuvi data -----------------
    def send_sensors(found_data):
        global time_interval, data_history, last_sent_timestamp, ui_buffer, token_expiration_time
        
        mac_address, sensor_data = found_data

        current_time = time.time()

        # Check if the token has to be refreshed
        if (token_expiration_time - int(current_time)) <= token_update_time:
            refresh_user_token()

        if time_stamps[mac_address] is None or (current_time - time_stamps[mac_address]) >= time_interval * 60:
            time_stamps[mac_address] = current_time
            if len(data_history) == 10:
                data_history.pop(0)

            utc_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            entry = {"timestamp": utc_timestamp, "mac": mac_address, "data": sensor_data}
            data_history.append(entry)

            db.child("users").child(user_uid).child("devices").child(device_uuid).child(mac_address).set(
                {
                    'utc_timestamp': utc_timestamp,
                    'temperature': sensor_data['temperature'], 
                    'humidity': sensor_data['humidity'], 
                    'pressure': sensor_data['pressure'], 
                    'rssi': sensor_data['rssi'],
                    'battery': sensor_data['battery']
                }, 
                id_token
            )

            str_list = []

            for e in data_history:
                str_list.append(
                    f"({e['timestamp']}) {e['mac']}: "
                    f"T: {e['data']['temperature']} \u00B0C "
                    f"P: {e['data']['pressure']} hPa "
                    f"H: {e['data']['humidity']} %"
                )

            ui_clear()
            ui_title("Broadcasting...", 80)
            ui_table("History", str_list, 80)
            ui_commands(["Stop broadcasting"])
            ui_buffer += "\nEnter command: "
            print(ui_buffer, end="", flush=True)
    #--------------------------------------------------------------------------

    # send_sensors_thread - Thread for sending Ruuvi-data ---------------------
    def send_sensors_thread():
        if len(filtered_macs) == 0: 
            RuuviTagSensor.get_data(send_sensors, None, run_flag)
        else:
            RuuviTagSensor.get_data(send_sensors, filtered_macs, run_flag)
    #--------------------------------------------------------------------------

    # back - Go back to the Main Screen ---------------------------------------
    def back():
        global data_history, broadcasting
        nonlocal finished, settings_path
        run_flag.running = False
        data_history = []
        broadcasting = False
        finished = True

        if (os.path.isfile(settings_path)):
             os.remove(settings_path)

    #--------------------------------------------------------------------------

    run_flag.running = True
    thread = threading.Thread(target=send_sensors_thread)
    thread.start()
    finished = False

    # Render loop...
    while not finished:
        str_list = []

        for e in data_history:
            str_list.append(
                f"({e['timestamp']}) {e['mac']}: "
                f"T: {e['data']['temperature']} \u00B0C "
                f"P: {e['data']['pressure']} hPa "
                f"H: {e['data']['humidity']} %"
            )

        ui_clear()
        ui_title("Broadcasting...", 80)
        ui_table("History", str_list, 80)
        # Print and handle commands
        ui_commands(["Stop broadcasting"])
        ui_enter_command("Enter command: ", [back])


#------------------------------------------------------------------------------
# scanning_screen - Scanning Screen
#------------------------------------------------------------------------------
def scanning_screen():
    ui_clear()

    ui_update = True            # Should the screen to be updated?
    run_flag.running = True     # Scanning flag
    unique_macs.clear()
    filtered_macs.clear()

    # scan_sensors - Callback function for the Ruuvi scan --------------------
    def scan_sensors(found_data):
        global ui_buffer
        nonlocal ui_update

        mac_address, sensor_data = found_data
        if mac_address not in unique_macs:
            unique_macs.add(mac_address)
            ui_update = True

        if ui_update == True:
            mac_list = list(unique_macs)

            ui_clear()
            ui_title("Scanning for sensors...", 80)
            ui_table("Discovered Sensors:", mac_list, 80)
            ui_commands(["Stop scanning"])
            ui_buffer += "\nEnter command: "
            print(ui_buffer, end="", flush=True)

            ui_update = False
    #--------------------------------------------------------------------------

    # scan_sensors_thread - Thread for scanning Ruuvi-sensors -----------------
    def scan_sensors_thread():
        RuuviTagSensor.get_data(scan_sensors, None, run_flag)
    #--------------------------------------------------------------------------

    # back - Go back to the Sensors Screen ------------------------------------
    def back():
        nonlocal finished
        run_flag.running = False
        finished = True
    #--------------------------------------------------------------------------

    # Create a thread for scanning Ruuvi-sensors
    thread = threading.Thread(target=scan_sensors_thread, daemon=True)
    thread.start()
    finished = False
    
    # Render loop...
    while not finished:
        mac_list = list(unique_macs)
        ui_clear()
        ui_title("Scanning for sensors...", 80)
        ui_table("Discovered Sensors:", mac_list, 80)
        ui_commands(["Stop scanning"])
        ui_enter_command("Enter command: ", [back])


#------------------------------------------------------------------------------
# add_remove_sensor_screen - Add / Remove Sensor Screen
#------------------------------------------------------------------------------
def add_remove_sensor_screen():
    finished = False

    # add_remove - Moves sensors from table to table ---------------------------
    def add_remove():
        nonlocal unique_macs_list
        nonlocal difference
        sensor_number = input("Enter sensor number: ")

        if sensor_number.isdigit():
            sensor_number = int(sensor_number)
            
            if sensor_number > 0 and sensor_number <= len(unique_macs_list):
                mac = unique_macs_list[sensor_number - 1]

                if mac in difference:
                    filtered_macs.add(mac)
                    difference = unique_macs - filtered_macs
                else:
                    filtered_macs.remove(mac)
                    difference = unique_macs - filtered_macs
    #--------------------------------------------------------------------------
    
    # automatic - Set up automatic filtering ----------------------------------
    def automatic():
        nonlocal finished
        filtered_macs.clear()
        finished = True
    #--------------------------------------------------------------------------

    # back - Go back to the Sensors Screen ------------------------------------
    def back():
        nonlocal finished
        finished = True
    #--------------------------------------------------------------------------

    # Render loop...
    while not finished:
        unique_macs_list = list(unique_macs)
        filtered_macs_list = list(filtered_macs)
        difference = unique_macs - filtered_macs
        difference_list = list(difference)

        # Available sensors...
        for i in range(len(difference_list)):
            difference_list[i] = f"{unique_macs_list.index(difference_list[i]) + 1}: {difference_list[i]}"

        # Filtered sensors...
        for i in range(len(filtered_macs_list)):
            filtered_macs_list[i] = f"{unique_macs_list.index(filtered_macs_list[i]) + 1}: {filtered_macs_list[i]}"

        ui_clear()
        ui_title("Add / Remove", 80)
        ui_table("Sensors", difference_list, 80)
        ui_table("Filtered Sensors", list(filtered_macs_list), 80)

        # Print and handle commands...
        ui_commands(["Add / Remove", "Automatic", "Back"])
        ui_enter_command("Enter command: ", [add_remove, automatic, back])


#------------------------------------------------------------------------------
# sensors_screen - Sensors Screen
#------------------------------------------------------------------------------
def sensors_screen():
    finished = False

    # back - Go back to the Main Screen ---------------------------------------
    def back():
        nonlocal finished
        finished = True
    #--------------------------------------------------------------------------

    # Render loop...
    while not finished:
        ui_clear()
        ui_title("Sensors", 80)

        # Print discovered sensors table...
        if len(unique_macs) == 0:
            ui_table("Discovered Sensors:", ["None"], 80)
        else:
            mac_list = list(unique_macs)
            ui_table("Discovered Sensors:", mac_list, 80)

        # Print filtered sensors table...
        if len(filtered_macs) == 0:
            ui_table("Filtered Sensors:", ["Automatic"], 80)
        else:
            mac_list = list(filtered_macs)
            ui_table("Filtered Sensorrs:", mac_list, 80)
        
        # Print commands...
        if len(unique_macs) == 0:
            ui_commands(["Start scanning", "Back"])
        else:
            ui_commands(["Start scanning", "Add / Remove", "Back"])

        # Handle commands...
        if len(unique_macs) == 0:
            ui_enter_command("Enter command: ", [scanning_screen, back])
        else:
            ui_enter_command("Enter command: ", [scanning_screen, add_remove_sensor_screen, back])


#------------------------------------------------------------------------------
# uuid_screen - Device UUID Screen
#------------------------------------------------------------------------------
def uuid_screen():
    global device_uuid
    finished = False

    # generate_uuid - Generates a new Device UUID and saves it ----------------
    def generate_uuid():
        global device_uuid
        device_uuid = uuid.uuid1()

        try:
            with open("uuid.txt", 'w') as f:
                f.write(str(device_uuid))
        except IOError:
            print("uuid.txt error: Could not write to file")
            exit()
    #--------------------------------------------------------------------------

    # back - Go back to the Main Screen ---------------------------------------
    def back():
        nonlocal finished
        finished = True
    #--------------------------------------------------------------------------

    # Render loop...
    while not finished:
        ui_clear()
        ui_title("Device UUID", 80)
        ui_table("Device UUID:", [str(device_uuid)], 80)
        ui_commands(["Generate new Device UUID", "Back"])
        ui_enter_command("Enter command: ", [generate_uuid, back])


#------------------------------------------------------------------------------
# interval_screen - Time Interval Screen
#------------------------------------------------------------------------------
def interval_screen():
    finished = False

    # change_interval - Aks user to give a new time interval ------------------
    def change_interval():
        global time_interval
        interval = input("Enter new Time Interval: ")

        if interval.isdigit():
            time_interval = int(interval)
            if time_interval == 0:
                time_interval = 1
    #--------------------------------------------------------------------------

    # back - Go back to the Main Screen ---------------------------------------
    def back():
        nonlocal finished
        finished = True
    #--------------------------------------------------------------------------

    # Render loop...
    while not finished:
        ui_clear()
        ui_title("Time Interval", 80)
        if time_interval == 1:
            ui_table("Time Interval:", ["1 minute"], 80)
        else:
            ui_table("Time Interval:", [str(time_interval) + " minutes"], 80)
        ui_commands(["Change Time Interval", "Back"])
        ui_enter_command("Enter command: ", [change_interval, back])


#------------------------------------------------------------------------------
# login_screen - Login screen
#------------------------------------------------------------------------------
def login_screen():
    global ui_buffer, user_uid, id_token, refresh_token, token_expiration_time, broadcasting

    settings_path = "./settings.txt"

    # Does settings.txt exists?
    if (os.path.isfile(settings_path)):
        # Yes...
        # Read the settings from settings.txt
        try:
            with open(settings_path, 'r') as f:
                lines = f.readlines()
                user_uid = lines[0].strip()
                refresh_token = lines[1].strip()
        except IOError:
            print("settings.txt: Could not read file")
            exit()
        
        broadcasting = True
        main_screen()

    ui_clear()
    ui_title("IsoLinna Control Panel v.1.0", 80)
    ui_title("Login", 80)
    print(ui_buffer)

    email = input("- Enter email: ")
    password = getpass.getpass("- Enter password: ")

    try:
        user = auth.sign_in_with_email_and_password(email, password)
    except:
        print("\033[1m\033[91mError: Invalid email or password\033[0m")
        exit()

    user_uid = user['localId']
    id_token = user['idToken']
    refresh_token = user['refreshToken']
    token_expiration_time = int(time.time()) + int(user['expiresIn'])

#------------------------------------------------------------------------------
# main_screen - Main screen
#------------------------------------------------------------------------------
def main_screen():
    global user_uid, device_uuid
    login = True

    if broadcasting == True:
        broadcast_screen()

    # log_out - Logs out the user ---------------------------------------------
    def log_out():
        nonlocal login
        global stop_refresh
        stop_refresh = True
        login = False
    #--------------------------------------------------------------------------

    while login:
        user_string = f"User UID:      {user_uid}"
        uuid_string = f"Device UUID:   {device_uuid}"

        if time_interval == 1:
            time_string = "Time Interval: 1 minute"
        else:
            time_string = f"Time Interval: {time_interval} minutes"

        ui_clear()
        ui_title("IsoLinna Control Panel v.1.0", 80)
        ui_table("Device Information", [user_string, uuid_string, time_string], 80)
        ui_commands(["Start broadcasting", "Sensors", "Device UUID", "Time Interval", "Log out", "Quit"])
        ui_enter_command("Enter command: ", [broadcast_screen, sensors_screen, uuid_screen, interval_screen, log_out, exit])

#-----------------------------------------------------------------------------#

#-----------------------------------------------------------------------------#
#                                    MAIN                                     #
#-----------------------------------------------------------------------------#
def main():
    global device_uuid, firebaseConfig, firebase, auth, db, broadcasting

    uuid_path = './uuid.txt'

    # Does uuid.txt exists?
    if (os.path.isfile(uuid_path)):
        # Yes...
        # Read the Device UUID from uuid.txt
        try:
            with open(uuid_path, 'r') as f:
                device_uuid = f.read()
        except IOError:
            print("uuid.txt: Could not read file")
            exit()
    else:
        # No...
        # Generate a new unique Device UUID
        device_uuid = uuid.uuid1()
        # ...and write it to uuid.txt
        try:
            with open(uuid_path, 'w') as f:
                f.write(str(device_uuid))
                print("uuid.txt: File created successfully.")
        except IOError:
            print("uuid.txt: Could not create file")
            exit()

    # Load Firebase configuration from isolinna.json
    try:
        with open("isolinna.json", 'r') as file:
            firebaseConfig = json.load(file)
    except IOError:
        print("isolinna.json: Could not read file")
        exit()
    
    # Setup Firebase
    firebase = pyrebase.initialize_app(firebaseConfig)
    auth = firebase.auth()
    db = firebase.database()

    # Main loop...
    while True:
        login_screen()
        main_screen()

if __name__ == "__main__":
    main()