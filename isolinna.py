import os
import json
import uuid
import pyrebase
import time
import threading
from datetime import datetime, timezone
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel
from rich import box
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.console import Group
from rich import print
from rich.markup import escape
import logging

# NOTE: This must be set before importing ruuvitag_sensor.
os.environ["RUUVI_BLE_ADAPTER"] = "bluez"

from ruuvitag_sensor.ruuvi import RuuviTagSensor, RunFlag

# Turn off the warnings because of ruuvitag_sensor
logging.basicConfig(level=logging.ERROR)

# Global Constants
SETTINGS_PATH = 'settings.json'
FIREBASE_CONF_PATH = 'isolinna.json'
TOKEN_UPDATE_DURATION = 1800    # 1800 seconds = 30 minutes

settings = {}

firebaseConfig = {}
firebase = None
auth = None
db = None

discovered_sensors = []
run_flag = RunFlag()
time_stamps = {}

console = Console()

#-----------------------------------------------------------------------------#
#                                 UI RELATED                                  #
#-----------------------------------------------------------------------------#

#-----------------------------------------------------------------------------#
# ui_title - Prints a title                                                   #
#-----------------------------------------------------------------------------#
def ui_title(title: str, width: int = 80):
    console.print(Panel(Text(title, justify="center"), style="bold green", width=width))

#-----------------------------------------------------------------------------#
# ui_commmands - Prints and handles the commands                              #
#-----------------------------------------------------------------------------#
def ui_commands(commands_strings: list, commands: list):
    # Print each command with its index number
    for i, command in enumerate(commands_strings, 1):
        console.print(f"{i}. [white]{command}")

    console.print()

    # Handle input
    command = IntPrompt.ask("Enter command")

    if command <= 0 or command > len(commands):
        return
    else:
        commands[command - 1]()

#-----------------------------------------------------------------------------#
#                                   SCREENS                                   #
#-----------------------------------------------------------------------------#

#-----------------------------------------------------------------------------#
# broadcasting_screen - Broadcasting Screen                                   #
#-----------------------------------------------------------------------------#
def broadcasting_screen():
    global settings, SETTINGS_PATH, run_flag
    finished = False
    data_history = []

    if not settings['broadcasting']:
        settings['broadcasting'] = True

        try:
            with open(SETTINGS_PATH, 'w') as f:
                json.dump(settings, f, indent=4)
        except IOError:
            print(f"[bold red]{SETTINGS_PATH}: Could not write file. Please check if you have write permissions.")
            exit(1)

    # refresh_user_token - Refreshes the user's token -------------------------
    def refresh_user_token():
        global settings, SETTINGS_PATH

        try:
            tokens = auth.refresh(settings['refresh_token'])
            settings['id_token'] = tokens['idToken']
            settings['refresh_token'] = tokens['refreshToken']
            settings['token_expiration_time'] = int(time.time()) + 3600 # 3600 seconds = 1 hour
        except Exception as e:
            print("Error refreshing token: ", e)

        try:
            with open(SETTINGS_PATH, 'w') as f:
                json.dump(settings, f, indent=4)
        except IOError:
            print(f"[bold red]{SETTINGS_PATH}: Could not create or write file")
            exit()
    #--------------------------------------------------------------------------

    # send_sensors - Callback function for sending Ruuvi data -----------------
    def send_sensors(found_data):
        global settings
        nonlocal data_history

        mac_address, sensor_data = found_data

        if sensor_data['data_format'] < 5:
            return
        
        current_time = time.time()

        # Check if the token has to be refreshed
        if (settings['token_expiration_time'] - int(current_time)) <= TOKEN_UPDATE_DURATION:
            refresh_user_token()

        if time_stamps.get(mac_address) is None or (current_time - time_stamps[mac_address]) >= settings['time_interval'] * 60:
            time_stamps[mac_address] = current_time
            if len(data_history) == 10:
                data_history.pop(0)

            utc_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            entry = {"timestamp": utc_timestamp, "mac": mac_address, "data": sensor_data}
            data_history.append(entry)

            db.child("users").child(settings['user_uid']).child("devices").child(settings['device_uuid']).child(mac_address).push(
                {
                    'utc_timestamp': utc_timestamp,
                    'temperature': sensor_data['temperature'], 
                    'humidity': sensor_data['humidity'], 
                    'pressure': sensor_data['pressure'], 
                    'rssi': sensor_data['rssi'],
                    'battery': sensor_data['battery']
                }, 
                settings['id_token']
            )

            db.child("users").child(settings['user_uid']).child("devices").child(settings['device_uuid']).child("new_values").child(mac_address).update(
                {
                    'utc_timestamp': utc_timestamp,
                    'temperature': sensor_data['temperature'], 
                    'humidity': sensor_data['humidity'], 
                    'pressure': sensor_data['pressure'], 
                    'rssi': sensor_data['rssi'],
                    'battery': sensor_data['battery']
                }, 
                settings['id_token']
            )

            console.clear()
            ui_title("Broadcasting...")
            console.print()

            # Print table
            table = Table(title="Recent Events", width=80, box=box.ROUNDED, style="green", title_style="bold green")

            table.add_column("Time", justify="center", style="cyan", header_style="bold cyan")
            table.add_column("Sensor", justify="center", style="blue", header_style="bold blue")
            table.add_column("Data", justify="center", style="white", header_style="bold white")

            for data in data_history:
                data_str = f"Temperature: {data['data']['temperature']} \u00B0C, Humidity: {data['data']['humidity']} %, Pressure: {data['data']['pressure']} hPa, RSSI: {data['data']['rssi']} dBm, Battery: {(data['data']['battery']) / 1000} V"
                table.add_row(Text(datetime.strptime(data['timestamp'], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")), Text(data['mac']), Text(data_str))

            print(table)

            # List of commands
            commands_strings = ["Stop broadcasting"]

            # Print each command with its index number
            for i, command in enumerate(commands_strings, 1):
                console.print(f"{i}. {command}")

            console.print()
            print("Enter command: ", end="", flush=True)

    #--------------------------------------------------------------------------

    # send_sensors_thread - Thread for sending Ruuvi-data ---------------------
    def send_sensors_thread():
        if len(settings['followed_sensors']) == 0: 
            RuuviTagSensor.get_data(send_sensors, None, run_flag)
        else:
            RuuviTagSensor.get_data(send_sensors, settings['followed_sensors'], run_flag)
    #--------------------------------------------------------------------------

    run_flag.running = True
    thread = threading.Thread(target=send_sensors_thread)
    thread.start()

    # back - Save settings and go back to the Main Screen ---------------------
    def back():
        global settings, SETTINGS_PATH
        nonlocal finished

        finished = True
        run_flag.running = False
        settings['broadcasting'] = False

        try:
            with open(SETTINGS_PATH, 'w') as f:
                json.dump(settings, f, indent=4)
        except IOError:
            print(f"[bold red]{SETTINGS_PATH}: Could not write file. Please check if you have write permissions.")
            exit(1)
    #--------------------------------------------------------------------------

    # Render loop...
    while not finished:
        console.clear()
        ui_title("Broadcasting...")

        # Print table
        table = Table(title="Recent Events", width=80, box=box.ROUNDED, style="green", title_style="bold green")

        table.add_column("Time", justify="center", style="cyan", header_style="bold cyan")
        table.add_column("Sensor", justify="center", style="blue", header_style="bold blue")
        table.add_column("Data", justify="center", style="white", header_style="bold white")

        for data in data_history:
            data_str = f"Temperature: {data['data']['temperature']} \u00B0C, Humidity: {data['data']['humidity']} %, Pressure: {data['data']['pressure']} hPa, RSSI: {data['data']['rssi']} dBm, Battery: {(data['data']['battery']) / 1000} V"
            table.add_row(Text(datetime.strptime(data['timestamp'], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")), Text(data['mac']), Text(data_str))

        print(table)

        # List of commands
        commands_strings = ["Stop broadcasting"]
        commands = [back]

        # Handle commands
        ui_commands(commands_strings, commands)

#-----------------------------------------------------------------------------#
# scanning_screen - Scanning Screen                                           #
#-----------------------------------------------------------------------------#
def scanning_screen():
    global discovered_sensors, settings, run_flag
    finished = False
    run_flag.running = True     # Scanning flag
    ui_update = True            # Should the screen to be updated?
    new_discoveries = []

    # back - Go back to Main Screen -------------------------------------------
    def back():
        global run_flag
        nonlocal finished
        run_flag.running = False
        finished = True
    #--------------------------------------------------------------------------

    # scan_sensors - Callback function for the Ruuvi get_data -----------------
    def scan_sensors(found_data):
        global ui_buffer, discovered_sensors
        nonlocal ui_update

        mac_address, sensor_data = found_data

        if mac_address not in discovered_sensors:
            if sensor_data['data_format'] < 5:
                return
            
            discovered_sensors.append(mac_address)
            new_discoveries.append(mac_address)
            ui_update = True

        if ui_update == True:
            console.clear()
            ui_title("Scanning sensors...")
            # Print newly discovered sensors...
            if len(new_discoveries) == 0:
                console.print(Panel(Text(""), title="[bold green]New Discovered Sensors", style="green", width=80))
            else:
                discoveries_text = Text("\n".join(new_discoveries), style="bold white")
                console.print(Panel(discoveries_text, title="[bold green]New Discovered Sensors", style="green", width=80))
            
            # List of commands
            commands_strings = ["Stop scanning"]
            commands = [back]

            # Print each command with its index number
            for i, command in enumerate(commands_strings, 1):
                console.print(f"{i}. {command}")

            console.print()
            print("Enter command: ", end="", flush=True)

            ui_update = False
    #--------------------------------------------------------------------------

    # scan_sensors_thread - Thread for scanning Ruuvi-sensors -----------------
    def scan_sensors_thread():
        RuuviTagSensor.get_data(scan_sensors, None, run_flag)
    #--------------------------------------------------------------------------

    # Create a thread for scanning Ruuvi-sensors
    thread = threading.Thread(target=scan_sensors_thread, daemon=True)
    thread.start()
    finished = False

    # Render loop...
    while not finished:
        console.clear()
        ui_title("Scanning sensors...")
        
        # Print newly discovered sensors...
        if len(new_discoveries) == 0:
            console.print(Panel(Text(""), title="[bold green]New Discovered Sensors", style="green", width=80))
        else:
            discoveries_text = Text("\n".join(new_discoveries), style="bold white")
            console.print(Panel(discoveries_text, title="[bold green]New Discovered Sensors", style="green", width=80))
        
        # List of commands
        commands_strings = ["Stop scanning"]
        commands = [back]

        # Handle commands
        ui_commands(commands_strings, commands)

#-----------------------------------------------------------------------------#
# follow_unfollow_sensors_screen - Follow / Unfollow Sensors Screen           #
#-----------------------------------------------------------------------------#
def follow_unfollow_sensors_screen():
    global discovered_sensors, settings
    finished = False
    discovered_sensors_strings = []

    # follow_unfollow - Follow or unfollow a sensor ---------------------------
    def follow_unfollow():
        global discovered_sensors, settings

        sensor_number = IntPrompt.ask("Enter sensor number")
            
        if sensor_number > 0 and sensor_number <= len(discovered_sensors):
            mac = discovered_sensors[sensor_number - 1]

            if mac in settings['followed_sensors']:
                settings['followed_sensors'].remove(mac)
            else:
                settings['followed_sensors'].append(mac)
    #--------------------------------------------------------------------------
    
    # automatic - Set up automatic filtering ----------------------------------
    def automatic():
        nonlocal finished
        settings['followed_sensors'] = []
        finished = True
    #--------------------------------------------------------------------------

    # back - Go back to the Sensors Screen ------------------------------------
    def back():
        nonlocal finished
        finished = True
    #--------------------------------------------------------------------------

    # Render loop...
    while not finished:
        # Discovered sensors strings...
        discovered_sensors_strings = [f"{index + 1}: {sensor}" for index, sensor in enumerate(discovered_sensors)]
        
        # Followed sensors strings...
        followed_sensors_sorted = sorted(
            settings['followed_sensors'], 
            key=lambda sensor: discovered_sensors.index(sensor) + 1
        )

        followed_sensors_strings = [
            f"{discovered_sensors.index(sensor) + 1}: {sensor}" for sensor in followed_sensors_sorted
        ]


        console.clear()
        ui_title("Follow / Unfollow Sensors")

        # Print discovered sensors...
        discovered_text = Text("\n".join(discovered_sensors_strings), style="bold white")
        console.print(Panel(discovered_text, title="[bold green]Discovered Sensors", style="green", width=80))

        # Print followed sensors...
        if len(settings['followed_sensors']) == 0:
            console.print(Panel(Text("Automatic", style="bold white"), title="[bold green]Followed Sensors", style="green", width=80))
        else:
            followed_text = Text("\n".join(followed_sensors_strings), style="bold white")
            console.print(Panel(followed_text, title="[bold green]Followed Sensors", style="green", width=80))

        # List of commands
        commands_strings = ["Follow / Unfollow", "Automatic", "Back"]
        commands = [follow_unfollow, automatic, back]
        
        # Handle commands
        ui_commands(commands_strings, commands)
        
        discovered_sensors_strings.clear()
        followed_sensors_strings.clear()

#-----------------------------------------------------------------------------#
# sensors_screen - Sensors Screen                                             #
#-----------------------------------------------------------------------------#
def sensors_screen():
    global settings, discovered_sensors
    finished = False

    if len(discovered_sensors) == 0:
        if len(settings['followed_sensors']) > 0:
            discovered_sensors = settings['followed_sensors'].copy()

    # back - Save settings and go back to Main Screen -------------------------
    def back():
        global settings, SETTINGS_PATH
        nonlocal finished
        finished = True

        try:
            with open(SETTINGS_PATH, 'w') as f:
                json.dump(settings, f, indent=4)
        except IOError:
            print(f"[bold red]{SETTINGS_PATH}: Could not write file. Please check if you have write permissions.")
            exit(1)
    #--------------------------------------------------------------------------

    def clear_sensors():
        global discovered_sensors, settings
        discovered_sensors.clear()
        settings['followed_sensors'] = []

    # Render loop...
    while not finished:
        console.clear()
        ui_title("Sensors")

        # Print discovered sensors...
        if len(discovered_sensors) == 0:
            console.print(Panel(Text("None", style="bold white"), title="[bold green]Discovered Sensors", style="green", width=80))
        else:
            discovered_text = Text("\n".join(discovered_sensors), style="bold white")
            console.print(Panel(discovered_text, title="[bold green]Discovered Sensors", style="green", width=80))

        # Print followed sensors...
        if len(settings['followed_sensors']) == 0:
            console.print(Panel(Text("Automatic", style="bold white"), title="[bold green]Followed Sensors", style="green", width=80))
        else:
            followed_text = Text("\n".join(settings['followed_sensors']), style="bold white")
            console.print(Panel(followed_text, title="[bold green]Followed Sensors", style="green", width=80))

        # List of commands
        if len(discovered_sensors) == 0:
            commands_strings = ["Start scanning", "Back"]
            commands = [scanning_screen, back]
        else:
            commands_strings = ["Start scanning", "Follow / Unfollow Sensors", "Clear Sensors", "Back"]
            commands = [scanning_screen, follow_unfollow_sensors_screen, clear_sensors, back]

        # Handle commands
        ui_commands(commands_strings, commands)

#-----------------------------------------------------------------------------#
# settings_screen - Settings screen                                           #
#-----------------------------------------------------------------------------#
def settings_screen():
    global settings
    finished = False

    # back - Save settings and go back to Main Screen -------------------------
    def back():
        global settings, SETTINGS_PATH
        nonlocal finished
        finished = True

        # Save settings to settings-file
        try:
            with open(SETTINGS_PATH, 'w') as f:
                json.dump(settings, f, indent=4)
        except IOError:
            print(f"[bold red]{SETTINGS_PATH}: Could not write file. Please check if you have write permissions.")
            exit(1)
    #--------------------------------------------------------------------------

    # generate_uuid - Generates a new Deivce UUID -----------------------------
    def generate_uuid():
        settings['device_uuid'] = str(uuid.uuid1())  # Generate UUID and convert it to string
    #--------------------------------------------------------------------------

    # Render loop...
    while not finished:
        device_uuid_column = Columns([Text("Device UUID:", style="bold blue"), Text(settings['device_uuid'], style="white")])

        if (settings['time_interval'] == 1):
            time_interval_column = Columns([Text("Time Interval:", style="bold blue"), Text("1 minute", style="white")] )
        else:
            time_interval_column = Columns([Text("Time Interval:", style="bold blue"), Text(str(settings['time_interval']) + " minutes", style="white")])

        rows = Group(device_uuid_column, time_interval_column)

        # prompt_uuid - Device UUID Prompt ------------------------------------
        def prompt_uuid():
            nonlocal rows
            
            console.clear()
            console.print(Panel(Text("Settings", justify="center"), style="bold green", width=80))
        
            # Print device information
            console.print(Panel(rows, title="[bold green]Device Settings", style="green", width=80))

            # List of commands
            commands_strings = ["Generate new Device UUID", "Cancel"]
            commands = [generate_uuid]

            # Handle commands
            ui_commands(commands_strings, commands)
        #----------------------------------------------------------------------

        # prompt_time_interval - Time Interval Prompt -------------------------
        def prompt_time_interval():
            nonlocal rows
            console.clear()
            ui_title("Settings")
        
            # Print device information
            console.print(Panel(rows, title="[bold green]Device Settings", style="green", width=80))

            # Handle input
            time_interval = IntPrompt.ask("Enter new time interval (Enter 0 to Cancel)")

            if time_interval <= 0:
                return
            else:
                settings['time_interval'] = time_interval
        #----------------------------------------------------------------------

        console.clear()
        ui_title("Settings")
        
        # Print device information
        console.print(Panel(rows, title="[bold green]Device Settings", style="green", width=80))
        
        # List of commands
        commands_strings = ["Generate new Device UUID", "Time Interval", "Back"]
        commands = [prompt_uuid, prompt_time_interval, back]

        # Handle commands
        ui_commands(commands_strings, commands)

#-----------------------------------------------------------------------------#
# main_screen - Main screen                                                   #
#-----------------------------------------------------------------------------#
def main_screen():
    global settings
    login = True

    if (settings['broadcasting'] == True):
        broadcasting_screen()

    # log_out - Logs out the user ---------------------------------------------
    def log_out():
        global settings, SETTINGS_PATH, validate_positive
        nonlocal login
        login = False
        
        # Remove User UID, ID Token and Refresh Token from the settings
        del settings['user_uid']
        del settings['refresh_token']
        del settings['id_token']
        del settings['token_expiration_time']

        # Save settings to settings-file
        try:
            with open(SETTINGS_PATH, 'w') as f:
                json.dump(settings, f, indent=4)
        except IOError:
            print(f"[bold red]{SETTINGS_PATH}: Could not write file. Please check if you have write permissions.")
            exit(1)
    #--------------------------------------------------------------------------

    # Render loop...
    while login:
        console.clear()
        user_uuid_column = Columns([Text("User UID:", style="bold blue"), Text(settings['user_uid'], style="white")])
        device_uuid_column = Columns([Text("Device UUID:", style="bold blue"), Text(settings['device_uuid'], style="white")])

        if (settings['time_interval'] == 1):
            time_interval_column = Columns([Text("Time Interval:", style="bold blue"), Text("1 minute", style="white")] )
        else:
            time_interval_column = Columns([Text("Time Interval:", style="bold blue"), Text(str(settings['time_interval']) + " minutes", style="white")])

        rows = Group(user_uuid_column, device_uuid_column, time_interval_column)

        # Print title
        ui_title("IsoLinna Control Panel v.1.1")

        # Print device information
        console.print(Panel(rows, title="[bold green]Device Information", style="green", width=80))
        
        # Print followed sensors
        if len(settings['followed_sensors']) == 0:
            console.print(Panel(Text("Automatic", style="bold white"), title="[bold green]Followed Sensors", style="green", width=80))
        else:
            followed_text = Text("\n".join(settings['followed_sensors']), style="bold white")
            console.print(Panel(followed_text, title="[bold green]Followed Sensors", style="green", width=80))

        # List of commands
        commands_strings = ["Start broadcasting", "Sensors", "Settings", "Log out", "Quit"]
        commands = [broadcasting_screen, sensors_screen, settings_screen, log_out, exit]

        # Handle commands
        ui_commands(commands_strings, commands)

#-----------------------------------------------------------------------------#
# login_screen - Login screen                                                 #
#-----------------------------------------------------------------------------#
def login_screen():
    global settings

    if 'user_uid' in settings and 'refresh_token' in settings:
        main_screen()

    console.clear()
    ui_title("IsoLinna Control Panel v.1.1")
    ui_title("Login")
    console.print()
    email = Prompt.ask("Enter your email-address")
    password = Prompt.ask("Enter your password", password=True)

    #email = "tomi.isojarvi@isolinna.com"
    #password = "1234567890"

    try:
        user = auth.sign_in_with_email_and_password(email, password)
    except:
        print("[bold red]Login Error: Invalid email or password")
        exit(1)

    settings['user_uid'] = user['localId']
    settings['id_token'] = user['idToken']
    settings['refresh_token'] = user['refreshToken']
    settings['token_expiration_time'] = int(time.time()) + int(user['expiresIn'])

    # Save settings to settings-file
    try:
        with open(SETTINGS_PATH, 'w') as f:
            json.dump(settings, f, indent=4) 
    except IOError:
        print(f"{SETTINGS_PATH}: Could not write file. Please check if you have write permissions.")
        exit(1)

#-----------------------------------------------------------------------------#
#                                    MAIN                                     #
#-----------------------------------------------------------------------------#
def main():
    global SETTINGS_PATH, FIREBASE_CONF_PATH, settings, firebaseConfig, firebase, auth, db

    # Load settings...

    # Does settings-file exists?
    if os.path.isfile(SETTINGS_PATH):
        # Yes...
        # Is the file empty?
        if os.path.getsize(SETTINGS_PATH) > 0:
            # No ...    
            # Read the settings from settings-file
            try:
                with open(SETTINGS_PATH, 'r') as f:
                    settings = json.load(f)
            except IOError:
                print(f"[bold red]{SETTINGS_PATH}: Could not open file. Please check the file's permissions.")
                exit(1)
            except json.JSONDecodeError:
                print(f"[bold red]{SETTINGS_PATH}: File contains invalid JSON.")
                exit(1)
        else:
            # Yes...
            print(f"[bold red]{SETTINGS_PATH}: File is empty.")
            exit(1)
    else:
        # No...
        # Create default settings
        settings['time_interval'] = 1
        settings['device_uuid'] = str(uuid.uuid1())  # Generate UUID and convert it to string
        settings["broadcasting"] = False
        settings['followed_sensors'] = []

        # Save settings to settings-file
        try:
            with open(SETTINGS_PATH, 'w') as f:
                json.dump(settings, f, indent=4) 
        except IOError:
            print(f"[bold red]{SETTINGS_PATH}: Could not write file. Please check if you have write permissions.")
            exit(1)

    # Load Firebase configuration...

    # Does configuration-file exists?
    if os.path.isfile(FIREBASE_CONF_PATH):
        # Yes...
        # Is the file empty?
        if os.path.getsize(FIREBASE_CONF_PATH) > 0:
            # No ...    
            # Read the configuration from configuration-file
            try:
                with open(FIREBASE_CONF_PATH, 'r') as f:
                    firebaseConfig = json.load(f)
            except IOError:
                print(f"[bold red]{FIREBASE_CONF_PATH}: Could not open file. Please check the file's permissions.")
                exit(1)
            except json.JSONDecodeError:
                print(f"[bold red]{FIREBASE_CONF_PATH}: File contains invalid JSON.")
                exit(1)
        else:
            # Yes...
            print(f"[bold red]{FIREBASE_CONF_PATH}: File is empty.")
            exit(1)
    else:
        # No...
        print(f"[bold red]{FIREBASE_CONF_PATH}: File does not exist.")
        exit(1)

    # Setup Firebase...

    try:
        firebase = pyrebase.initialize_app(firebaseConfig)
        auth = firebase.auth()
        db = firebase.database()
    except KeyError as e:
        print(f"[bold red]Firebase configuration is missing a required key: {e}")
        exit(1) 
    except ValueError as e:
        print(f"[bold red]Invalid value in Firebase configuration: {e}")
        exit(1)
    except TypeError as e:
        print(f"[bold red]Incorrect Firebase configuration format: {e}")
        exit(1)
    except AttributeError as e:
        print(f"[bold red]Error initializing Firebase services: {e}")
        exit(1)
    except Exception as e:  # Catch-all for unexpected errors
        print(f"[bold red]An unexpected error occurred: {e}")
        exit(1)

    
    # Main loop...
    while True:
        login_screen()
        main_screen()

if __name__ == "__main__":
    main()