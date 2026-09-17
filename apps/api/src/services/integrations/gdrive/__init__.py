"""Google Drive integration for video activities.

Instance-wide, opt-in storage backend that keeps a video activity's file on the
operator's personal Google Drive instead of the server. The package is split
into:

- ``errors``       — ``GDriveError`` hierarchy and the HTTP mapping
- ``credentials``  — OAuth client secrets / token file handling and refresh
- ``client``       — thin async Drive REST v3 client over ``httpx``
- ``readiness``    — cached readiness state (flag + credential + token)
- ``service``      — upload / cleanup orchestration used by the activity services
- ``authorize``    — one-time OAuth consent flow driven by ``cli.py``

``authorize`` pulls in ``google-auth-oauthlib`` lazily and is deliberately NOT
imported here so the API process never loads it.
"""
