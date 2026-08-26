import legacy_app as core
from service_fleet import register

register(core)
app = core.app


@app.route("/api/vehicles", endpoint="api_vehicles_compat")
def api_vehicles_compat():
    return core.jsonify(core.load_vehicles())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(core.os.environ.get("PORT", 5000)), debug=False)
