# hass-poem-ilevel
Home Assistant custom component for the [Poem iLevel oil tank monitor](https://poemtechnology.com/shop/ilevel/).

## Configuration:
```yaml
# Example configuration.yaml
sensor:
   - platform: poem_ilevel
     username: !secret ilevel_email
     password: !secret ilevel_password
```

### Configuration Options:

* **username**: *Your_myilevel.com_username*.
* **password**: *Your_myilevel.com_password*.

## Sensor
Provides a sensor named `sensor.ilevel_xxxx` where `xxxx` is a unique number provided by the iLevel API with the value of `integer % full` and following Attributes:

```json
{
    "icon": "mdi:gauge" # changes based on current fullness
   ,"capacity": 330     # this is in gallons
   ,"gallons": 211      # current level in gallons
   ,"inches": 28        # current level in inches
   ,"unit_of_measurement": "%"
   ,"friendly_name": "Oil level"
   ,"attribution": "Oil level measured with iLevel by Poem Technology."
}
```
