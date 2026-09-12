# Bot Admin Android

Separate Android panel for the Telegram bot.

## Before building

Open `app/src/main/java/com/moroznya/botadmin/MainActivity.kt` and replace:

```kotlin
private val apiBaseUrl = "https://YOUR-SERVER.example.com"
```

with the public HTTPS address where the bot API is running.

On the server, configure:

```text
ADMIN_API_TOKEN=<long random secret>
ADMIN_OWNER_ID=7545068007
ADMIN_PASSWORD=Martinez231107
```

The APK uses the login endpoint to obtain the API token and stores the
successful session in Android private `SharedPreferences`. The password is not
stored after login.

## Build

Open this folder in Android Studio and run **Build > Build APK(s)**. The debug
APK will be placed under:

```text
app/build/outputs/apk/debug/app-debug.apk
```

The project targets Android 8.0+ and is suitable for installation on a Poco
device after enabling installation from the chosen file manager.
