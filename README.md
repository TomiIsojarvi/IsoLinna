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
  "apiKey": "Your Firebase API key",
  "authDomain": "Domain name used for Firebase Authentication",
  "databaseURL": "URL of your Firebase Realtime Database",
  "projectId": "Your project ID",
  "storageBucket": "URL of your Firebase Storage bucket",
  "messagingSenderId": "Your Firebase Cloud Messaging ID",
  "appId": "Your App ID",
  "measurementId": ""
}
```
