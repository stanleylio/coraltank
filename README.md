# coraltank

Scripts for a custom aquarium temperature controller designed for experiments at [HIMB](http://gatescorallab.com/). The controller is designed to regulate the temperature of a water tank to follow a given temperature profile. Instead of using heaters, it controls solenoid valves for hot and cold water (supplied externally) feeding into the tank. Tank temperature is monitored by submerged temperature probe(s). The implementation is simple; the interesting bits perhaps are the requirements:

## Requirements for the thermostat

- The controller shall regulate the temperature of a water tank to track a given temperature profile
- History of the actual water temperature in the past `N` days must be kept
    - `N` be configurable by the user
    - The data must not fill up the local storage (small SD cards)
- The thermostat function must continue with or without network
    - So no external dependency
    - This doesn't preclude the rest of the controller to use external services for say telemetry, as long as network interruption doesn't interfere with thermostat operation
- The solenoid valves must be cycled periodically even when thermostat is inactive (e.g. no ongoing experiment and thus no profile to track)
- Valve state must fail-safe. If the fault condition is unrecoverable (e.g. temperature sensor malfunction), the valves (especially the hot valve) must not stay open
- I say "controller" here, but they are typically deployed dozens at a time. Organization of code and configurations must minimize the amount of bootstrapping work required for each controller (ideally, clone the same SD card image and run perhaps at most one bash script)

## Requirements for the user interface (the Control Panel)

Through the Control Panel, the user can:

- Upload new temperature profiles (as time series of setpoints stored as CSV)
- Download the current temperature profile
- Suspend the thermostat operation temporarily (e.g. for maintenance or calibration)
    - With auto-resume after timeout
    - Audio and/or visual warning when suspended would be nice
- Enable/disable the thermostat function ("deployed" vs. "maintenance" mode)
- Upload operation notes/logs to the controller to be timestamped and recorded
- Provide temperature readings from external temperature references (for calibration)
- Locate and identify a controller among a roomful of identical-looking ones ("honk when the Lock button on the key fob is pressed twice")
- View and download the temperature history, the operation logs, and critical events
- Do all of the above one-handed, on a phone/tablet, outdoor, under the sun, while both hands are wet with seawater

## Dependency

This implementation depends on these awesome projects:

- [Apache](https://www.apache.org/) and [mod_wsgi](https://modwsgi.readthedocs.io/en/master/)
- [Bootstrap](https://getbootstrap.com) and [jQuery](https://jquery.com/) (don't judge)
- [DataTables](https://datatables.net/)
- [DB Browser for SQLite](https://sqlitebrowser.org/)
- [Flask](https://flask.palletsprojects.com/)
- [RabbitMQ](https://www.rabbitmq.com/)
- [Raspbian Lite](https://www.raspberrypi.org/software/)
- [Redis](https://redis.io/)
- [Supervisor](http://supervisord.org/)

The list is not exhaustive of course; just listing the stuff that needs to be installed separately after `git clone`. There might be stuff from other open source projects; naturally their own licenses take precedence.
