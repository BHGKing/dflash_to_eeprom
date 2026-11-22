# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Identity of the build that is running.

A source checkout leaves both strings empty. The release workflow overwrites
this file after the tests pass and before PyInstaller runs, so the published
executable knows the version tag it was built from.
"""

RELEASE_TAG = ""
COMMIT = ""
