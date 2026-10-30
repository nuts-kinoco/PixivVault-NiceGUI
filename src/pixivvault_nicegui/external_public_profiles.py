"""Fixed original public protocol profiles from 3ac3aa0. No user credentials."""
_APP_VERSION = "8.6.10"

_APP_OS_VERSION = "26.5"

_DEVICE_MODEL = "iPhone16,2"

_USER_AGENT = f"PixivIOSApp/{_APP_VERSION} (iOS {_APP_OS_VERSION}; {_DEVICE_MODEL})"

_X_CLIENT_HASH_SECRET = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c"

OAUTH_PROFILES = (
    {
        "name": "ios",
        "client_id": "MOZAS22mBQEBcDxe7HBS0ZPh6ebD",
        "client_secret": "hb2Kubr2gE8xW1hSBuYBDAYBcModerate",
        "ua": _USER_AGENT,
        "os": "ios",
        "os_version": _APP_OS_VERSION,
        "version": _APP_VERSION,
        "salt": _X_CLIENT_HASH_SECRET,
    },
    {
        "name": "android",
        "client_id": "b5c8448b1114e7560a87612c2957b988",
        "client_secret": "28c1f40015a385b4f7b5b3e2b9817dcd",
        "ua": "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)",
        "os": "android",
        "os_version": "11",
        "version": "5.0.234",
        "salt": "28c1f40015a385b4f7b5b3e2b9817dcd",
    },
)
