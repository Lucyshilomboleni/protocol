"""
EA-BLE Simulator - Flask web application.

Run:   python app.py
Open:  http://127.0.0.1:5000

The page has one tab button per objective. When a button such as
"Run simulation" is pressed, the browser sends a request to one of the
/api/... routes below; Python runs the discrete-event simulation and
returns the results as JSON, which the page shows in a table.
"""
from flask import Flask, render_template, request, jsonify
from simulator import experiments as ex

app = Flask(__name__)


def _number(name, default, low, high):
    """Read a number from the request and keep it inside safe limits (input validation)."""
    data = request.get_json(silent=True) or {}
    try:
        value = float(data.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _scenario():
    data = request.get_json(silent=True) or {}
    sc = data.get('scenario', 'office')
    return sc if sc in ex.SCENARIO_NAMES else 'office'


# Limits for every start value the user can type: (default, lowest, highest)
LIMITS = dict(hours=(12, 1, 48), soc=(50, 0, 100), capacitance=(100, 10, 1000),
              distance=(10, 1, 50), seed=(1000, 1, 99999), runs=(1, 1, 5), harvest=(100, 0, 2000))


def _start(max_hours=48):
    """Read all the start values typed by the user, each kept inside its limits."""
    v = {k: _number(k, *LIMITS[k]) for k in ('hours', 'soc', 'capacitance', 'distance', 'seed')}
    v['hours'] = min(v['hours'], max_hours)
    v['seed'] = int(v['seed'])
    return v


# ---------------------------------------------------------------- page
@app.route('/')
def home():
    """The single page with the tab buttons (Home + four objectives)."""
    return render_template('index.html', info=ex.info(), limits=LIMITS)


# ---------------------------------------------------------------- API used by the tabs
@app.route('/api/model/run', methods=['POST'])
def api_model_run():                         # Objective 1
    return jsonify(ex.model_run(_scenario(), _start()))


@app.route('/api/protocol/decision', methods=['POST'])
def api_decision():                          # Objective 2
    return jsonify(ex.decision(_number('soc', *LIMITS['soc']), _number('harvest', *LIMITS['harvest']),
                               _number('capacitance', *LIMITS['capacitance'])))


@app.route('/api/protocol/run', methods=['POST'])
def api_protocol_run():                      # Objective 2
    return jsonify(ex.protocol_run(_start(max_hours=24)))


@app.route('/api/reserve/drawer', methods=['POST'])
def api_drawer():                            # Objective 3
    return jsonify(ex.drawer_test(_start()))


@app.route('/api/compare', methods=['POST'])
def api_compare():                           # Objective 4
    return jsonify(ex.compare(int(_number('runs', *LIMITS['runs'])), _start(max_hours=12)))


if __name__ == '__main__':
    app.run(debug=True)
