# EA-BLE Simulator (Flask web app)

Energy-Aware Bluetooth Low Energy advertising for a battery-free kinetic-energy-harvesting key locator.

## Run it

1. Install Python 3.10 or newer.
2. Open a terminal in this folder and run:

```
python -m venv venv
venv\Scripts\activate          (Windows)   or   source venv/bin/activate   (Mac/Linux)
pip install -r requirements.txt
python app.py
```

3. Open http://127.0.0.1:5000 in your browser.

## Project structure

```
app.py                     Flask routes: the page and the /api/... endpoints
simulator/engine.py        discrete-event simulation engine (harvester, supercapacitor, radio, protocols)
simulator/experiments.py   the experiment behind each objective tab
templates/base.html        layout with the tab buttons
templates/index.html       puts the five tab panels together
templates/tabs/*.html      one template per tab (home, objective1 ... objective4)
static/style.css           one style sheet for the whole app
static/app.js              tab switching and calls to the API
```

## How it works

Clicking a tab button shows that panel without reloading the page. Clicking a run button sends the inputs
to Flask as JSON; Python runs the simulation and returns the results, which are shown in tables.
