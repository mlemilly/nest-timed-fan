# Google Nest Integration with Timed Fan

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/mlemilly/nest-timed-fan.svg?style=flat-square)](https://github.com/mlemilly/nest-timed-fan/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)

This is a Home Assistant custom integration based on the [Google Nest integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/nest) from Home Assistant Core, with added support for timed fan control.

## Features

- **Nest Device Integration**: Full support for Google Nest devices
- **Timed Fan Control**: Extended fan timer capabilities
- **Climate Control**: Manage temperature and climate settings
- **Cameras**: Access to Nest camera feeds
- **Sensors**: Device sensors and status monitoring

## Installation

### Via HACS (Recommended)

1. Click on HACS in the Home Assistant sidebar
2. Select "Integrations"
3. Click the three dots (...) and select "Custom repositories"
4. Add the repository URL: `https://github.com/millariel/nest-timed-fan`
5. Select "Integration" as the category
6. Click "Install"
7. Restart Home Assistant

### Manual Installation

1. Download this repository as a ZIP file
2. Extract the contents to `<config>/custom_components/nest_timed_fan/`
3. Restart Home Assistant

## Configuration

### Prerequisites

- A Google account
- Access to Google Nest devices
- Google Cloud Project with SDM API enabled

### Setup

1. Go to **Settings** → **Devices & Services** → **Create Integration**
2. Search for "Google Nest with Timed Fan"
3. Click the integration
4. Authorize your Google account when prompted
5. Select your Nest devices

## Requirements

- Home Assistant 2024.1.0 or later
- `google-nest-sdm==9.1.2` or compatible version
- Dependencies: `ffmpeg`, `http`, `application_credentials`

## License

This integration is based on code from Home Assistant Core and is licensed under the Apache License 2.0.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues, questions, or feature requests, please open an issue on the [GitHub repository](https://github.com/millariel/nest-timed-fan/issues).
