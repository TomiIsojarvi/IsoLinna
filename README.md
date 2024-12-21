# IsoLinna

## Installation
NOTE: You must install Pyrebase 4 which is a forked version of Pyrebase. IsoLinna will not work with the original Pyrebase.
```sh
pip install ruuvitag-sensor
pip install pyrebase4
```
Create a isolinna.json with following information:
```sh
{
  "apiKey": "Your project's API key",
  "authDomain": "Domain name used for your Firebase Authentication",
  "databaseURL": "URL of your Firebase Realtime Database",
  "storageBucket": "URL of your Firebase Storage bucket",
}
```
