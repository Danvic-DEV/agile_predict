# Home Assistant Integration

This folder contains example configurations to integrate Agile Predict's electricity price forecasts into your Home Assistant setup.

## What You'll Get

- **REST Sensor**: Automatically fetch ML-based price predictions every 30 minutes
- **ApexCharts Card**: Visual comparison of predicted vs actual Octopus Agile prices
- **Up to 13-day forecasts**: See upcoming price predictions with min/max ranges (default: 7 days)
- **Real-time updates**: Predictions improve as the model accumulates training data

## Prerequisites

1. **Home Assistant** installed and running
2. **Octopus Energy Integration** ([HACS](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy)) - for actual price comparison
3. **ApexCharts Card** ([HACS](https://github.com/RomRider/apexcharts-card)) - for the price chart
4. **Octopus Agile tariff** - any UK region (X, A, B, C, D, E, F, G, H, J, K, L, M, N, P)

## Installation

### Step 1: Add the REST Sensor

1. Open your Home Assistant `configuration.yaml` (or create a separate `sensors.yaml` file and include it)
2. Copy the contents of [`sensor.yaml`](sensor.yaml)
3. **Important**: Change `region=G` in the URL to your Octopus Agile region code
4. Optionally adjust `scan_interval` (default: 1800 seconds = 30 minutes)
5. Restart Home Assistant

### Step 2: Verify the Sensor

1. Go to **Developer Tools** → **States**
2. Search for `sensor.agile_predict`
3. You should see the sensor with attributes including `prices` (array of predictions)

### Step 3: Add the ApexCharts Card (Optional)

1. Open your Home Assistant Dashboard
2. Click **Edit Dashboard** → **Add Card** → **Manual Card**
3. Copy the contents of [`apex_chart.yaml`](apex_chart.yaml)
4. **Important**: Replace `19m*******_************` with your actual Octopus meter MPAN and serial number
   - Find this in your Octopus Energy integration entities
   - Look for entities like `sensor.octopus_energy_electricity_19m1234567_1234567890123_current_rate`
5. Save the card

## Configuration Options

### Sensor Configuration

```yaml
sensor:
  - platform: rest
    resource: https://agilepredict.danvic.dev/api/v1/forecasts/prices?region=G&days=7&forecast_count=1&high_low=true
    scan_interval: 1800
    name: Agile Predict
    value_template: "{{ value_json[0]['name'] }}"
    json_attributes_path: "$[0]"
    json_attributes:
      - "created_at"
      - "prices"
```

**Parameters:**
- `region`: Your Octopus Agile region code (A-P, excluding I and O, plus X for all regions)
- `days`: Forecast window (1-14 days, default: 7)
- `forecast_count`: Must be 1 (multiple forecasts not supported in public API)
- `high_low`: Must be true (includes min/max range for uncertainty)
- `scan_interval`: Update frequency in seconds (recommended: 1800-3600)

**Region Codes:**
- **X**: All regions (average)
- **A**: Eastern England
- **B**: East Midlands
- **C**: London
- **D**: Merseyside and Northern Wales
- **E**: West Midlands
- **F**: North Eastern England
- **G**: North Western England
- **H**: Southern England
- **J**: South Eastern England
- **K**: Southern Wales
- **L**: South Western England
- **M**: Yorkshire
- **N**: Southern Scotland
- **P**: Northern Scotland

### Chart Customization

The ApexCharts card supports extensive customization:

- **Graph span**: Change `graph_span: 7d` to `3d`, `1d`, etc.
- **Price range**: Adjust `min: -2` in the yaxis config
- **Colors**: Change `color: red` or `color: yellow` to any CSS color
- **Opacity**: Adjust transparency with `opacity: 0.1` to `1.0`
- **Hide predicted range**: Remove or comment out the "Predicted Range (min-max)" series

## Data Structure

The sensor exposes the following data:

### State Value
The forecast name (e.g., `bundle::update-job-seed`)

### Attributes

```json
{
  "created_at": "2026-03-22T12:34:56Z",
  "prices": [
    {
      "date_time": "2026-03-22T00:00:00Z",
      "agile_pred": 22.50,
      "agile_low": 21.80,
      "agile_high": 23.20,
      "agile_actual": null,
      "region": "G"
    },
    ...
  ]
}
```

**Fields:**
- `date_time`: Timestamp for the half-hour slot (ISO 8601 format)
- `agile_pred`: Predicted price in pence per kWh
- `agile_low`: Lower bound of prediction (uncertainty range)
- `agile_high`: Upper bound of prediction (uncertainty range)
- `agile_actual`: Actual price once known (null for future slots)
- `region`: Region code

## Automation Examples

### Example 1: Notify When Cheap Period Coming

```yaml
automation:
  - alias: "Agile: Notify Cheap Period"
    trigger:
      - platform: template
        value_template: >
          {% set prices = state_attr('sensor.agile_predict', 'prices') %}
          {% set next_hour = prices | selectattr('date_time', 'gt', now().isoformat()) | list %}
          {% if next_hour | length > 0 %}
            {{ next_hour[0].agile_pred < 10 }}
          {% else %}
            false
          {% endif %}
    action:
      - service: notify.mobile_app
        data:
          title: "Cheap Electricity Coming"
          message: "Next slot predicted below 10p/kWh - good time to charge!"
```

### Example 2: Set Helper Based on Next 3 Hours Average

```yaml
automation:
  - alias: "Agile: Update 3-Hour Average"
    trigger:
      - platform: state
        entity_id: sensor.agile_predict
    action:
      - service: input_number.set_value
        target:
          entity_id: input_number.next_3h_avg_price
        data:
          value: >
            {% set prices = state_attr('sensor.agile_predict', 'prices') %}
            {% set upcoming = prices | selectattr('date_time', 'gt', now().isoformat()) | list %}
            {% set next_6 = upcoming[:6] %}
            {{ (next_6 | map(attribute='agile_pred') | list | average) | round(2) }}
```

## Troubleshooting

### Sensor shows "unavailable"

1. Check Home Assistant logs for REST sensor errors
2. Verify the API is accessible: visit https://agilepredict.danvic.dev/ in a browser
3. Ensure your region code is correct
4. Try increasing `scan_interval` if rate-limited

### Chart not displaying

1. Verify ApexCharts card is installed via HACS
2. Check that `sensor.agile_predict` exists and has `prices` attribute
3. Ensure Octopus Energy entities exist and are named correctly
4. Check browser console for JavaScript errors

### Predictions seem inaccurate

- Model accuracy improves over time as training data accumulates
- Expect significant improvements at 1 month (~1,400 samples) and 6 months (~8,600 samples)
- Check the "Training Data" indicator on https://agilepredict.danvic.dev/ to see current training size

## API Documentation

For advanced usage, see the full API documentation:

- **Availability**: `GET /api/v1/forecasts/availability` - Cache metadata and warmed regions
- **Regions**: `GET /api/v1/forecasts/regions` - List of available region codes
- **Prices**: `GET /api/v1/forecasts/prices` - Price predictions (used by this sensor)

All endpoints are read-only and served from an in-memory cache (no authentication required).

## Support

- **Public UI**: https://agilepredict.danvic.dev/
- **GitHub Issues**: Report bugs or request features
- **Home Assistant Community**: Share your configurations and automations

## License

These configuration examples are provided as-is for use with the Agile Predict public API.
