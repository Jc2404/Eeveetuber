# Server composition

The executable composition root currently lives in `eeveetuber.api.app:create_app` so it is packaged
with the Python distribution. This directory is reserved for deployment assets and process-level
configuration; domain logic must not be added here.

