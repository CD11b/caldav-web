# CalDAV Web Client

A simple web-based client for viewing CalDAV task data.

## Features

* Connects to any standard CalDAV server (e.g., Radicale, Baïkal)
* Displays task events in a web interface
* Built using Python and Flask
* Simple configuration and local deployment

## Demo

![demo](images/demo.gif)

## Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/CD11b/caldav-web.git
   cd caldav-web
   ```

2. **Create a virtual environment and install dependencies:**

   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Generate the configuration:**

   ```bash
   python generate_config.py
   ```

   This will prompt you for your CalDAV server URL, username, and password, and save them in a config file.

4. **Run the web client:**

   ```bash
   python app.py
   ```

   Open your browser and go to: [http://127.0.0.1:5000](http://127.0.0.1:5000)