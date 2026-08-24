from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, session, jsonify
from pathlib import Path
from werkzeug.utils import secure_filename
from datetime import datetime, date
import base64
import json, os, uuid
import requests

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "vehicles.json"
IMAGES_DIR = APP_DIR / "static" / "images"
DOCS_DIR = APP_DIR / "static" / "documents"

ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip() or "main"
GITHUB_API = "https://api.github.com"
DATOVA_KOSTKA_API_KEY = os.environ.get("DATOVA_KOSTKA_API_KEY", "").strip()
DATOVA_KOSTKA_URL = "https://api.dataovozidlech.cz/api/vehicletechnicaldata/v2"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-crocodille-rent")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

def github_sync_enabled(): return bool(GITHUB_TOKEN and GITHUB_REPO and GITHUB_BRANCH)
def github_headers(): return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}
def github_content_url(repo_path): return f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{str(repo_path).replace(chr(92),'/').lstrip('/')}"
def github_get_file_sha(repo_path):
    if not github_sync_enabled(): return None
    r=requests.get(github_content_url(repo_path),headers=github_headers(),params={"ref":GITHUB_BRANCH},timeout=20)
    if r.status_code==404:return None
    r.raise_for_status();return r.json().get("sha")
def github_put_text(repo_path,text,message):
    if not github_sync_enabled():return None
    p={"message":message,"content":base64.b64encode(text.encode()).decode(),"branch":GITHUB_BRANCH};sha=github_get_file_sha(repo_path)
    if sha:p["sha"]=sha
    r=requests.put(github_content_url(repo_path),headers=github_headers(),json=p,timeout=30);r.raise_for_status();return r.json()
def github_put_binary(repo_path,file_path,message):
    if not github_sync_enabled():return None
    p={"message":message,"content":base64.b64encode(Path(file_path).read_bytes()).decode(),"branch":GITHUB_BRANCH};sha=github_get_file_sha(repo_path)
    if sha:p["sha"]=sha
    r=requests.put(github_content_url(repo_path),headers=github_headers(),json=p,timeout=45);r.raise_for_status();return r.json()
def github_delete_file(repo_path,message):
    if not github_sync_enabled():return None
    sha=github_get_file_sha(repo_path)
    if not sha:return None
    r=requests.delete(github_content_url(repo_path),headers=github_headers(),json={"message":message,"sha":sha,"branch":GITHUB_BRANCH},timeout=30);r.raise_for_status();return r.json()
def backup_current_vehicles():
    if github_sync_enabled() and DATA_FILE.exists():
        t=datetime.now().strftime("%Y%m%d_%H%M%S");github_put_text(f"backups/vehicles_{t}_{uuid.uuid4().hex[:6]}.json",DATA_FILE.read_text(),f"Záloha vehicles.json {t}")
def sync_vehicles_to_github(message="Aktualizace vehicles.json"):
    if github_sync_enabled() and DATA_FILE.exists():github_put_text("vehicles.json",DATA_FILE.read_text(),message)
def sync_local_file_to_github(local_path,repo_path,message):
    if github_sync_enabled():github_put_binary(repo_path,local_path,message)
def safe_github_sync(callback):
    if not github_sync_enabled():return
    try:callback()
    except Exception as e:print(f"GitHub sync failed: {e}")
def load_vehicles():return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else []
def save_vehicles(vehicles,commit_message="Aktualizace vehicles.json"):
    safe_github_sync(backup_current_vehicles);DATA_FILE.write_text(json.dumps(vehicles,ensure_ascii=False,indent=2));safe_github_sync(lambda:sync_vehicles_to_github(commit_message))
def get_vehicle(vehicle_id):return next((v for v in load_vehicles() if str(v.get("id"))==str(vehicle_id)),None)
def update_vehicle(vehicle_id,updater,commit_message=None):
    vehicles=load_vehicles()
    for i,v in enumerate(vehicles):
        if str(v.get("id"))==str(vehicle_id):vehicles[i]=updater(v);save_vehicles(vehicles,commit_message or f"Upraveno vozidlo {vehicle_id}");return vehicles[i]
def parse_date(value):
    try:return datetime.strptime(str(value)[:10],"%Y-%m-%d").date() if value else None
    except:return None
def days_until(value):
    d=parse_date(value);return (d-date.today()).days if d else None
def is_active_vehicle(v):
    s=str(v.get("status","")).strip().lower();return not(s.startswith("vráceno") or s.startswith("vraceno") or s in ("servis","v servisu","mimo provoz","odstaveno","odstavené"))
def status_for(value):
    d=parse_date(value)
    if not d:return("unknown","nezadáno")
    n=(d-date.today()).days
    if n<0:return("bad","propadlé")
    if n<=14:return("bad",f"{n} dní")
    if n<=45:return("soon",f"{n} dní")
    return("ok","OK")
def vehicle_alert(v):
    if not is_active_vehicle(v):return "inactive"
    c=[status_for(v.get(k))[0] for k in("stk_until","vignette_until","liability_until","casco_until") if v.get(k)]
    return "bad" if "bad" in c else "soon" if "soon" in c else "ok" if "ok" in c else "unknown"
def vehicle_alert_items(v):
    if not is_active_vehicle(v):return []
    r=[]
    for k,l in(("stk_until","STK"),("vignette_until","Dálniční známka"),("liability_until","Povinné ručení"),("casco_until","Havarijní pojištění")):
        if v.get(k):
            s,t=status_for(v.get(k))
            if s in("bad","soon"):r.append({"key":k,"label":l,"state":s,"text":t})
    return r
def _flatten_dict(value,out=None):
    out={} if out is None else out
    if isinstance(value,dict):
        for k,x in value.items():
            if isinstance(x,(dict,list)):_flatten_dict(x,out)
            elif x not in(None,""):out[str(k).lower()]=x
    elif isinstance(value,list):
        for x in value:_flatten_dict(x,out)
    return out
def _flatten_details(value,prefix="",out=None):
    out=[] if out is None else out
    if isinstance(value,dict):
        for k,x in value.items():
            l=f"{prefix} / {k}" if prefix else str(k)
            if isinstance(x,(dict,list)):_flatten_details(x,l,out)
            elif x not in(None,""):out.append({"label":l,"value":str(x)})
    elif isinstance(value,list):
        for i,x in enumerate(value,1):_flatten_details(x,f"{prefix} {i}".strip(),out)
    return out
def _pick(flat,*keys):
    for k in keys:
        v=flat.get(k.lower())
        if v not in(None,""):return v
    return ""
def _brand_first(value):
    t=str(value or "").strip().upper()
    if "IVECO" in t:return "IVECO"
    if "RENAULT" in t:return "RENAULT"
    return t.split('/')[0].split()[0] if t else ""
def _model_name(value,brand=""):
    t=str(value or "").strip().upper();b=str(brand).upper()
    if "IVECO" in b or "DAILY" in t:return "DAILY"
    if "RENAULT" in b or "MASTER" in t:return "MASTER"
    return t.split('/')[0] if t else ""
def _fuel_name(value):return {"NM":"Nafta","BA":"Benzín","EL":"Elektřina","LPG":"LPG","CNG":"CNG"}.get(str(value or "").strip().upper(),str(value or "").strip())
def _clean_number(value):
    try:
        n=float(str(value).strip().replace(",","."));return str(int(n)) if n.is_integer() else str(n)
    except:return str(value or "").strip()
def _power_kw(value):
    t=str(value or "").strip();return _clean_number(t.split('/')[0].strip()) if t else ""
def datova_kostka_basic(data):
    f=_flatten_dict(data);fr=_pick(f,"DatumPrvniRegistrace","DatumPrvniRegistraceVCR","PrvniRegistrace");year=str(fr)[:4] if fr else _pick(f,"RokVyroby","Rok");brand=_brand_first(_pick(f,"TovarniZnacka","Znacka","Vyrobce"));modelraw=_pick(f,"ObchodniOznaceni","Model")
    return {"vin":_pick(f,"VIN"),"spz":_pick(f,"RegistracniZnacka","RZ","SPZ"),"brand":brand,"model":_model_name(modelraw,brand),"year":year,"first_registration":fr,"fuel":_fuel_name(_pick(f,"Palivo","DruhPaliva")),"engine_type":_pick(f,"MotorTyp","TypMotoru"),"engine_capacity":_clean_number(_pick(f,"MotorZdvihObjem","ZdvihovyObjem","ObjemMotoru")),"power_kw":_power_kw(_pick(f,"MotorMaxVykon","MaxVykon","Vykon")),"emission":_pick(f,"EmisniUroven","EmisniNorma"),"inspection_until":_pick(f,"PravidelnaTechnickaProhlidkaDo","TechnickaProhlidkaDo")}
def fetch_datova_kostka(vin):
    if not DATOVA_KOSTKA_API_KEY:raise RuntimeError("Na Renderu není nastaven DATOVA_KOSTKA_API_KEY.")
    vin=(vin or "").strip().upper()
    if len(vin)!=17:raise ValueError("VIN musí mít 17 znaků.")
    r=requests.get(DATOVA_KOSTKA_URL,params={"vin":vin},headers={"API_KEY":DATOVA_KOSTKA_API_KEY},timeout=30)
    if r.status_code in(401,403):raise RuntimeError("Datová kostka odmítla API klíč.")
    r.raise_for_status();p=r.json();data=p.get("Data",p.get("data",p)) if isinstance(p,dict) else p
    if not data:raise RuntimeError("Datová kostka pro tento VIN nevrátila žádná data.")
    return data
def make_datova_kostka(vin,data):return {"loaded_at":datetime.now().strftime("%Y-%m-%d %H:%M"),"vin":(vin or "").strip().upper(),"basic":datova_kostka_basic(data),"details":_flatten_details(data),"raw":data}
def hydrate_missing_datova_kostka(vehicles):
    if not DATOVA_KOSTKA_API_KEY:return 0,[]
    changed=0;errors=[]
    for v in vehicles:
        vin=(v.get("vin") or "").strip().upper()
        if len(vin)!=17 or v.get("datova_kostka"):continue
        try:v["datova_kostka"]=make_datova_kostka(vin,fetch_datova_kostka(vin));changed+=1
        except Exception as e:errors.append(f"{vin}: {e}")
    if changed:save_vehicles(vehicles,f"Automaticky doplněna Datová kostka pro {changed} vozidel")
    return changed,errors
def dashboard_data(vehicles):return {"total":len(vehicles),"active":len([v for v in vehicles if is_active_vehicle(v)]),"stk_30":0,"vignette_30":0,"insurance_30":0,"attention":[]}
@app.template_filter("czdate")
def czdate(value):
    d=parse_date(value);return d.strftime("%d.%m.%Y") if d else ""
@app.template_filter("czdate_short")
def czdate_short(value):
    d=parse_date(value);return f"{d.day}.{d.month}.{d.year}" if d else ""
@app.template_filter("km")
def km(value):
    try:return f"{int(value):,}".replace(","," ")+" km"
    except:return value or ""
@app.context_processor
def inject_helpers():return dict(status_for=status_for,vehicle_alert=vehicle_alert,vehicle_alert_items=vehicle_alert_items,is_active_vehicle=is_active_vehicle,admin_logged=session.get("admin") is True)
def require_admin():return session.get("admin") is True
@app.route("/")
def index():
    q=request.args.get("q","").strip().lower();vehicles=load_vehicles()
    if q:vehicles=[v for v in vehicles if q in " ".join(str(v.get(k,"")) for k in["spz","vin","brand","name","vehicle_id"]).lower()]
    return render_template("index.html",vehicles=vehicles,q=q)
@app.route("/v/<vehicle_id>")
def vehicle(vehicle_id):
    v=get_vehicle(vehicle_id);return render_template("vehicle.html",v=v) if v else (render_template("not_found.html",vehicle_id=vehicle_id),404)
@app.route("/admin/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        if request.form.get("pin")==ADMIN_PIN:session["admin"]=True;return redirect(url_for("admin"))
        flash("Špatný PIN.")
    return render_template("login.html")
@app.route("/admin/logout")
def logout():session.clear();return redirect(url_for("index"))
@app.route("/admin")
def admin():
    if not require_admin():return redirect(url_for("login"))
    vehicles=load_vehicles();loaded,errors=hydrate_missing_datova_kostka(vehicles)
    if loaded:flash(f"Automaticky načteny údaje z Datové kostky pro {loaded} vozidel.")
    elif errors:flash(f"Datovou kostku se nepodařilo načíst pro {len(errors)} vozidel.")
    return render_template("admin.html",vehicles=vehicles,dashboard=dashboard_data(vehicles))
@app.route("/admin/add-by-vin",methods=["POST"])
def add_vehicle_by_vin():
    if not require_admin():return redirect(url_for("login"))
    vin=(request.form.get("vin") or "").strip().upper()
    if len(vin)!=17:flash("VIN musí mít 17 znaků.");return redirect(url_for("admin"))
    vehicles=load_vehicles()
    for v in vehicles:
        if (v.get("vin") or "").strip().upper()==vin:flash("Vozidlo s tímto VIN už v evidenci existuje.");return redirect(url_for("edit",vehicle_id=v.get("id")))
    try:
        dk=make_datova_kostka(vin,fetch_datova_kostka(vin));b=dk["basic"];new_id=uuid.uuid4().hex[:10];nv={"id":new_id,"spz":b.get("spz") or "nezadáno","vehicle_id":"","name":b.get("model") or "Vozidlo","brand":b.get("brand") or "","model":b.get("model") or "","vin":vin,"year":str(b.get("year") or ""),"status":"V provozu","stk_until":str(b.get("inspection_until") or "")[:10],"vignette_until":"","liability_until":"","casco_until":"","assistance_until":"","next_service_date":"","next_service_km":"","next_service_note":"","note":"","photo":"","documents":[],"service_records":[],"datova_kostka":dk};vehicles.append(nv);save_vehicles(vehicles,f"Přidáno vozidlo podle VIN {vin}");flash("Vozidlo bylo vytvořeno podle VIN a údajů z Datové kostky.");return redirect(url_for("edit",vehicle_id=new_id))
    except Exception as e:flash(str(e));return redirect(url_for("admin"))
@app.route("/admin/<vehicle_id>",methods=["GET","POST"])
def edit(vehicle_id):
    if not require_admin():return redirect(url_for("login"))
    v=get_vehicle(vehicle_id)
    if not v:return render_template("not_found.html",vehicle_id=vehicle_id),404
    if request.method=="POST":
        fields=["spz","vehicle_id","brand","name","model","vin","year","km","status","stk_until","vignette_until","liability_until","casco_until","assistance_until","next_service_date","next_service_km","next_service_note","note"]
        def updater(x):
            for f in fields:
                if f in request.form:x[f]=(request.form.get(f) or "").strip()
            photo=request.files.get("photo")
            if photo and photo.filename:
                ext=Path(photo.filename).suffix.lower();fname=secure_filename(f"{x.get('id')}_{uuid.uuid4().hex[:8]}{ext}");lp=IMAGES_DIR/fname;photo.save(lp);x["photo"]=fname;safe_github_sync(lambda:sync_local_file_to_github(lp,f"static/images/{fname}",f"Přidána fotka k vozidlu {x.get('spz') or x.get('id')}"))
            return x
        update_vehicle(vehicle_id,updater,f"Upraveno vozidlo {vehicle_id}");flash("Uloženo.");return redirect(url_for("edit",vehicle_id=vehicle_id))
    return render_template("edit.html",v=v)
@app.route("/admin/<vehicle_id>/datova-kostka",methods=["POST"])
def load_datova_kostka(vehicle_id):
    if not require_admin():return redirect(url_for("login"))
    v=get_vehicle(vehicle_id)
    try:
        dk=make_datova_kostka(v.get("vin"),fetch_datova_kostka(v.get("vin")));update_vehicle(vehicle_id,lambda x:{**x,"datova_kostka":dk},f"Načtena Datová kostka pro {v.get('vin')}");flash("Údaje z Datové kostky byly načteny.")
    except Exception as e:flash(str(e))
    return redirect(url_for("vehicle",vehicle_id=vehicle_id))
@app.route("/admin/<vehicle_id>/documents/add",methods=["POST"])
def add_document(vehicle_id):
    if not require_admin():return redirect(url_for("login"))
    f=request.files.get("document");title=request.form.get("title","Dokument").strip()
    if not f or not f.filename:flash("Soubor nebyl vybrán.");return redirect(url_for("edit",vehicle_id=vehicle_id))
    ext=Path(f.filename).suffix.lower();fname=secure_filename(f"{vehicle_id}_{uuid.uuid4().hex[:8]}_{secure_filename(title) or 'document'}{ext}");lp=DOCS_DIR/fname;f.save(lp);safe_github_sync(lambda:sync_local_file_to_github(lp,f"static/documents/{fname}",f"Přidán dokument k vozidlu {vehicle_id}"))
    def updater(v):v.setdefault("documents",[]).append({"id":uuid.uuid4().hex[:10],"title":title,"filename":fname,"original_name":f.filename,"type":ext.replace(".","").upper(),"uploaded_at":datetime.now().strftime("%Y-%m-%d %H:%M")});return v
    update_vehicle(vehicle_id,updater);flash("Dokument nahrán.");return redirect(url_for("edit",vehicle_id=vehicle_id))
@app.route("/admin/<vehicle_id>/documents/<doc_id>/delete",methods=["POST"])
def delete_document(vehicle_id,doc_id):
    if not require_admin():return redirect(url_for("login"))
    def updater(v):v["documents"]=[d for d in v.get("documents",[]) if d.get("id")!=doc_id];return v
    update_vehicle(vehicle_id,updater);flash("Dokument smazán.");return redirect(url_for("edit",vehicle_id=vehicle_id))
@app.route("/admin/<vehicle_id>/service/add",methods=["POST"])
def add_service(vehicle_id):
    if not require_admin():return redirect(url_for("login"))
    rec={"id":uuid.uuid4().hex[:10],"date":request.form.get("date",""),"km":request.form.get("km",""),"title":request.form.get("title",""),"next_service":request.form.get("next_service","")}
    def updater(v):v.setdefault("service_records",[]).append(rec);return v
    update_vehicle(vehicle_id,updater);flash("Servisní záznam přidán.");return redirect(url_for("edit",vehicle_id=vehicle_id))
@app.route("/admin/<vehicle_id>/service/<sid>/delete",methods=["POST"])
def delete_service(vehicle_id,sid):
    if not require_admin():return redirect(url_for("login"))
    def updater(v):v["service_records"]=[s for s in v.get("service_records",[]) if s.get("id")!=sid];return v
    update_vehicle(vehicle_id,updater);flash("Servisní záznam smazán.");return redirect(url_for("edit",vehicle_id=vehicle_id))
@app.route("/documents/<filename>")
def documents(filename):return send_from_directory(DOCS_DIR,filename)
@app.route("/qr")
def qr():return render_template("qr.html",vehicles=load_vehicles(),base=request.url_root.rstrip("/"))

# --- Mobilni API pro iPhone aplikaci ---
def _api_alerts_for_vehicle(v, horizon=30):
    alerts=[]
    if not is_active_vehicle(v):return alerts
    checks=[("stk_until","STK"),("vignette_until","Dálniční známka"),("liability_until","Povinné ručení"),("casco_until","Havarijní pojištění"),("assistance_until","Asistence"),("next_service_date","Servis")]
    for key,label in checks:
        value=v.get(key);days=days_until(value)
        if days is None or days>horizon:continue
        severity="overdue" if days<0 else "critical" if days<=7 else "warning" if days<=14 else "info"
        text=f"{label} je po termínu {abs(days)} dní" if days<0 else f"{label} končí dnes" if days==0 else f"{label} končí za {days} dní"
        alerts.append({"id":f"{v.get('id')}:{key}:{value}","vehicle_id":str(v.get('id') or ''),"spz":v.get('spz') or '',"kind":key,"title":label,"message":text,"date":str(value or '')[:10],"days":days,"severity":severity})
    return alerts

def _api_vehicle(v):
    dk=v.get("datova_kostka") or {};basic=dk.get("basic") or {}
    return {"id":str(v.get("id") or ''),"spz":v.get("spz") or '',"vehicle_number":v.get("vehicle_id") or '',"vin":v.get("vin") or '',"brand":basic.get("brand") or v.get("brand") or '',"model":basic.get("model") or v.get("model") or v.get("name") or '',"year":basic.get("year") or v.get("year") or '',"status":v.get("status") or 'V provozu',"km":v.get("km") or '',"stk_until":v.get("stk_until") or basic.get("inspection_until") or '',"vignette_until":v.get("vignette_until") or '',"liability_until":v.get("liability_until") or '',"casco_until":v.get("casco_until") or '',"assistance_until":v.get("assistance_until") or '',"next_service_date":v.get("next_service_date") or '',"next_service_km":v.get("next_service_km") or '',"fuel":str(basic.get("fuel") or v.get("fuel") or '').upper(),"engine_type":basic.get("engine_type") or v.get("engine_type") or '',"engine_capacity":basic.get("engine_capacity") or v.get("engine_volume") or '',"power_kw":basic.get("power_kw") or v.get("engine_power") or '',"emission":basic.get("emission") or v.get("emission_class") or '',"photo_url":url_for('static',filename='images/'+v.get('photo'),_external=True) if v.get('photo') else '',"alerts":_api_alerts_for_vehicle(v)}

@app.route('/api/v1/health')
def api_health():return jsonify({"ok":True,"service":"crocodille-fleet","version":1})
@app.route('/api/v1/vehicles')
def api_vehicles():return jsonify({"vehicles":[_api_vehicle(v) for v in load_vehicles()]})
@app.route('/api/v1/vehicles/<vehicle_id>')
def api_vehicle_detail(vehicle_id):
    v=get_vehicle(vehicle_id)
    return jsonify(_api_vehicle(v)) if v else (jsonify({"error":"Vozidlo nenalezeno"}),404)
@app.route('/api/v1/alerts')
def api_alerts():
    try:horizon=max(1,min(int(request.args.get('days','30')),365))
    except:horizon=30
    alerts=[]
    for v in load_vehicles():alerts.extend(_api_alerts_for_vehicle(v,horizon))
    alerts.sort(key=lambda x:x['days'])
    return jsonify({"days":horizon,"count":len(alerts),"alerts":alerts})

if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)