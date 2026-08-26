import legacy_app as core
from service_fleet import register

register(core)
app = core.app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(core.os.environ.get("PORT", 5000)), debug=False)
