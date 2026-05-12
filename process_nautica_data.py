"""
process_nautica_data.py (DEMO VERSION)

Applies boost factors at OUTPUT time only — starting_values.json
always stores RAW data. Change PV_BOOST / LOAD_REDUCTION anytime
and re-run to get new numbers cleanly.
"""
import json, sys, os, calendar
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

SAST = timezone(timedelta(hours=2))

# ══════════════════════════════════════════════════
# DEMO BOOST — change anytime, re-run processor
# ══════════════════════════════════════════════════
PV_BOOST = 1.30
LOAD_REDUCTION = 0.80

def apply_boost(d):
    """Boost a monthly/daily totals dict. Returns NEW dict.
    Preserves original export ratio — export scales with PV boost."""
    r = dict(d)
    orig_pv = r.get('PV Yield (kWh)', 0)
    orig_sc = r.get('Self-consumption (kWh)', 0)
    orig_export = r.get('Export (kWh)', 0)
    orig_import = r.get('Import (kWh)', 0)
    orig_cons = r.get('Consumption (kWh)', 0)
    if orig_cons <= 0 and (orig_sc > 0 or orig_import > 0):
        orig_cons = orig_sc + orig_import

    new_pv = orig_pv * PV_BOOST
    new_cons = orig_cons * LOAD_REDUCTION
    # Scale export proportionally to PV increase (more PV → more surplus)
    new_export = orig_export * PV_BOOST
    # SC = boosted PV minus boosted export
    new_sc = max(0, new_pv - new_export)
    # Import = what load still needs from grid after SC
    new_import = max(0, new_cons - new_sc)

    r['PV Yield (kWh)'] = round(new_pv, 3)
    r['Consumption (kWh)'] = round(new_cons, 3)
    r['Self-consumption (kWh)'] = round(new_sc, 3)
    r['Export (kWh)'] = round(new_export, 3)
    r['Import (kWh)'] = round(new_import, 3)
    if new_pv > 0:
        r['Self-consumption Rate (%)'] = round((new_sc / new_pv) * 100, 3)
    for f in ['Inverter Yield (kWh)', 'Theoretical Yield (kWh)', 'CO₂ Avoided (t)', 'Standard Coal Saved (t)', 'Revenue (R.)']:
        if f in r and r[f] > 0 and orig_pv > 0:
            r[f] = round(r[f] * (new_pv / orig_pv), 3)
    return r

def apply_boost_hourly(h):
    """Boost hourly arrays. Preserves original export pattern."""
    r = {'current_hour': h['current_hour'], 'pv': [], 'load': [], 'import': [], 'export': []}
    for i in range(24):
        pv = h['pv'][i] * PV_BOOST
        load = h['load'][i] * LOAD_REDUCTION
        exp = h['export'][i] * PV_BOOST  # scale export with PV
        sc = max(0, pv - exp)
        imp = max(0, load - sc)
        r['pv'].append(round(pv, 2))
        r['load'].append(round(load, 2))
        r['export'].append(round(exp, 2))
        r['import'].append(round(imp, 2))
    return r

def apply_boost_daily_record(rec):
    """Boost a daily_history record. Preserves export ratio."""
    r = dict(rec)
    orig_pv = r.get('pv', 0)
    orig_export = r.get('export', 0)
    orig_cons = r.get('consumption', 0)
    orig_sc = r.get('self_consumption', 0)
    if orig_cons <= 0 and (orig_sc > 0 or r.get('import', 0) > 0):
        orig_cons = orig_sc + r.get('import', 0)
    new_pv = orig_pv * PV_BOOST
    new_cons = orig_cons * LOAD_REDUCTION
    new_export = orig_export * PV_BOOST
    new_sc = max(0, new_pv - new_export)
    new_import = max(0, new_cons - new_sc)
    r['pv'] = round(new_pv, 2)
    r['consumption'] = round(new_cons, 2)
    r['self_consumption'] = round(new_sc, 2)
    r['export'] = round(new_export, 2)
    r['import'] = round(new_import, 2)
    if 'hourly' in r and isinstance(r['hourly'], dict):
        ho = dict(r['hourly'])
        if 'pv' in ho:
            hp = [round(v * PV_BOOST, 2) for v in ho['pv']]
            hl = [round(v * LOAD_REDUCTION, 2) for v in ho.get('load', [0]*24)]
            he_orig = ho.get('export', [0]*24)
            he = [round(v * PV_BOOST, 2) for v in he_orig]
            hi = []
            for i in range(min(24, len(hp))):
                sc_i = max(0, hp[i] - he[i])
                hi.append(round(max(0, hl[i] - sc_i), 2))
            ho['pv'] = hp; ho['load'] = hl; ho['export'] = he; ho['grid'] = hi
            r['hourly'] = ho
    return r

ADDITIVE_FIELDS = [
    "PV Yield (kWh)", "Inverter Yield (kWh)", "Export (kWh)", "Import (kWh)",
    "Consumption (kWh)", "Self-consumption (kWh)", "CO₂ Avoided (t)",
    "Standard Coal Saved (t)", "Revenue (R.)", "Charge (kWh)", "Discharge (kWh)",
    "Theoretical Yield (kWh)", "Loss Due to Export Limitation (kWh)", "Loss Due to Export Limitation(R.)",
]
MAX_FIELDS = ["Peak Power (kW)"]

def parse_daily_report(fp):
    df = pd.read_excel(fp, header=None, sheet_name=0); headers = df.iloc[1].tolist()
    combined = {}; rc = 0
    for idx in range(2, len(df)):
        row = df.iloc[idx]
        for i, h in enumerate(headers):
            if pd.isna(h) or h in ['Statistical Period', 'Total String Capacity (kWp)']: continue
            key = str(h).strip(); val = float(row.iloc[i]) if not pd.isna(row.iloc[i]) else 0.0
            if key in ADDITIVE_FIELDS: combined[key] = combined.get(key, 0.0) + val
            elif key in MAX_FIELDS: combined[key] = max(combined.get(key, 0.0), val)
            else: combined[key] = val
        rc += 1
    return combined if rc > 0 else None

def parse_hourly_arrays(fp):
    df = pd.read_excel(fp, header=None, sheet_name=0)
    headers = [str(h).strip() if not pd.isna(h) else '' for h in df.iloc[1].tolist()]
    pv_col = next((i for i, h in enumerate(headers) if h == 'PV Yield (kWh)'), None)
    exp_col = next((i for i, h in enumerate(headers) if h == 'Export (kWh)'), None)
    imp_col = next((i for i, h in enumerate(headers) if h == 'Import (kWh)'), None)
    pv_arr=[0.0]*24; imp_arr=[0.0]*24; exp_arr=[0.0]*24; load_arr=[0.0]*24; ch=0
    for idx in range(2, len(df)):
        row = df.iloc[idx]
        try: ts = pd.Timestamp(row.iloc[0]); hour = ts.hour
        except: continue
        pv = float(row.iloc[pv_col]) if pv_col and not pd.isna(row.iloc[pv_col]) else 0.0
        exp = float(row.iloc[exp_col]) if exp_col and not pd.isna(row.iloc[exp_col]) else 0.0
        imp = float(row.iloc[imp_col]) if imp_col and not pd.isna(row.iloc[imp_col]) else 0.0
        load = imp if pv <= 0 else (pv - exp + imp if exp > 0 else pv + imp)
        pv_arr[hour]=round(pv,2); imp_arr[hour]=round(imp,2); exp_arr[hour]=round(exp,2); load_arr[hour]=round(load,2); ch=hour
    return {'current_hour': ch, 'pv': pv_arr, 'import': imp_arr, 'export': exp_arr, 'load': load_arr}

def add_daily_to_month(md, dd):
    u = dict(md)
    for f in ADDITIVE_FIELDS: u[f] = round(u.get(f, 0.0) + dd.get(f, 0.0), 3)
    for f in MAX_FIELDS: u[f] = round(max(u.get(f, 0.0), dd.get(f, 0.0)), 3)
    pv = u.get("PV Yield (kWh)", 0); sc = u.get("Self-consumption (kWh)", 0)
    if pv > 0: u["Self-consumption Rate (%)"] = round((sc / pv) * 100, 3)
    return u

def recalc_year(monthly, yr):
    matching = {k: v for k, v in monthly.items() if k.startswith(yr)}
    if not matching: return None
    yt = {}
    for mv in matching.values():
        for f in ADDITIVE_FIELDS: yt[f] = yt.get(f, 0.0) + mv.get(f, 0.0)
        for f in MAX_FIELDS: yt[f] = max(yt.get(f, 0.0), mv.get(f, 0.0))
    for k in yt: yt[k] = round(yt[k], 3)
    pv = yt.get("PV Yield (kWh)", 0); sc = yt.get("Self-consumption (kWh)", 0)
    if pv > 0: yt["Self-consumption Rate (%)"] = round((sc / pv) * 100, 3)
    return yt

def calc_all_time(lifetime):
    t = {}
    for yv in lifetime.values():
        for f in ADDITIVE_FIELDS: t[f] = t.get(f, 0.0) + yv.get(f, 0.0)
        for f in MAX_FIELDS: t[f] = max(t.get(f, 0.0), yv.get(f, 0.0))
    for k in t: t[k] = round(t[k], 3)
    pv = t.get("PV Yield (kWh)", 0); sc = t.get("Self-consumption (kWh)", 0)
    if pv > 0: t["Self-consumption Rate (%)"] = round((sc / pv) * 100, 3)
    return t

def main():
    data_dir = Path("data")
    raw_file = data_dir / "nautica_raw.xlsx"
    starting_file = data_dir / "starting_values.json"
    output_file = data_dir / "nautica_processed.json"
    daily_hist_file = data_dir / "daily_history.json"
    hourly_file = data_dir / "hourly_generation.json"

    print("🔄 Processing Nautica data (DEMO)")
    print(f"   PV +{(PV_BOOST-1)*100:.0f}% | Load -{(1-LOAD_REDUCTION)*100:.0f}% | Applied at output only")

    if not raw_file.exists(): print(f"❌ {raw_file} not found"); sys.exit(1)
    if not starting_file.exists(): print(f"❌ {starting_file} not found"); sys.exit(1)

    daily_data = parse_daily_report(raw_file)
    if not daily_data: print("❌ No data"); sys.exit(1)
    hourly_arrays = parse_hourly_arrays(raw_file)
    data_hour = hourly_arrays['current_hour']

    # Calc consumption from RAW
    pv = daily_data.get('PV Yield (kWh)', 0); exp = daily_data.get('Export (kWh)', 0); imp = daily_data.get('Import (kWh)', 0)
    cons = imp if pv <= 0 else (pv - exp + imp if exp > 0 else pv + imp)
    daily_data['Consumption (kWh)'] = round(cons, 2)
    sc = max(0, pv - exp)
    daily_data['Self-consumption (kWh)'] = round(sc, 2)
    if pv > 0: daily_data['Self-consumption Rate (%)'] = round((sc / pv) * 100, 2)
    print(f"  Raw: PV={pv:,.0f} Load={cons:,.0f} Export={exp:,.0f} SC={sc:,.0f}")

    with open(starting_file) as f: starting = json.load(f)
    monthly = starting["monthly"]; lifetime = starting["lifetime"]
    now = datetime.now(SAST); cmk = now.strftime("%Y-%m"); today_str = now.strftime("%Y-%m-%d")

    # Same-day handling (RAW)
    lrd = starting.get("last_run_date", ""); ld = starting.get("last_daily", {}); ms = starting.get("month_seeded", "")
    if cmk not in monthly: monthly[cmk] = {}
    skip = False
    if ms == cmk and lrd == today_str and not ld:
        starting["last_daily"] = dict(daily_data); starting["month_seeded"] = ""; skip = True
    elif lrd == today_str and ld:
        for f in ADDITIVE_FIELDS: monthly[cmk][f] = monthly[cmk].get(f, 0.0) - ld.get(f, 0.0)
    elif lrd and lrd != today_str:
        if ms: starting["month_seeded"] = ""
    if not skip: monthly[cmk] = add_daily_to_month(monthly[cmk], daily_data)

    # Recalc lifetime from RAW
    for yr in sorted(set(k[:4] for k in monthly)):
        yt = recalc_year(monthly, yr)
        if yt:
            if yr in lifetime:
                for k in lifetime[yr]:
                    if k not in yt: yt[k] = lifetime[yr][k]
            lifetime[yr] = yt
    all_time = calc_all_time(lifetime)

    # ═══ BOOST for output ═══
    boosted_monthly = {k: apply_boost(v) for k, v in sorted(monthly.items())}
    boosted_lifetime = {}
    for yr in sorted(set(k[:4] for k in boosted_monthly)):
        yt = {}; mm = {k: v for k, v in boosted_monthly.items() if k.startswith(yr)}
        for mv in mm.values():
            for f in ADDITIVE_FIELDS: yt[f] = yt.get(f, 0.0) + mv.get(f, 0.0)
            for f in MAX_FIELDS: yt[f] = max(yt.get(f, 0.0), mv.get(f, 0.0))
        for k in yt: yt[k] = round(yt[k], 3)
        pvv = yt.get("PV Yield (kWh)", 0); scv = yt.get("Self-consumption (kWh)", 0)
        if pvv > 0: yt["Self-consumption Rate (%)"] = round((scv / pvv) * 100, 3)
        boosted_lifetime[yr] = yt
    boosted_all_time = calc_all_time(boosted_lifetime)
    boosted_daily = apply_boost(daily_data)
    boosted_hourly = apply_boost_hourly(hourly_arrays)

    bpv = boosted_daily.get('PV Yield (kWh)', 0); bcons = boosted_daily.get('Consumption (kWh)', 0)
    bexp = boosted_daily.get('Export (kWh)', 0); bsc = boosted_daily.get('Self-consumption (kWh)', 0)
    print(f"  Boosted: PV={bpv:,.0f} Load={bcons:,.0f} Export={bexp:,.0f} SC={bsc:,.0f}")

    # Savings from boosted
    fin_config_file = data_dir.parent / "config" / "Financial config.json"
    pvsyst_file = data_dir.parent / "config" / "pvsyst_predictions.json"
    savings_out = {"today": {}, "current_month": {}, "all_time": {}}
    try:
        if fin_config_file.exists() and pvsyst_file.exists():
            with open(fin_config_file) as f: fin = json.load(f)
            with open(pvsyst_file) as f: pvs = json.load(f)
            rates = fin.get("rates", {}); seasons = fin.get("seasons", {}); tou = fin.get("tou_schedule", {})
            dh = pvs.get("daily_hourly", {}); ec = fin.get("export_credits", {})
            def gti(hour, dt):
                s = seasons.get(str(dt.month), "low_demand"); wd = dt.weekday()
                t = "weekday" if wd < 5 else ("saturday" if wd == 5 else "sunday")
                sch = tou.get(s, {}).get(t, []); p = sch[hour] if hour < len(sch) else "off_peak"
                return rates.get(s, {}).get(p, 0), p
            def cds(sc_kwh, exp_kwh, dt):
                hp = dh.get(dt.strftime("%m-%d"), [0]*24); pt = sum(hp)
                ps = {"peak": 0, "standard": 0, "off_peak": 0, "total": 0}
                es = {"standard": 0, "off_peak": 0, "total": 0}
                if pt <= 0: return ps, es
                for h in range(24):
                    frac = hp[h] / pt; rate, period = gti(h, dt)
                    if sc_kwh > 0: s = sc_kwh * frac * rate; ps[period] += s; ps["total"] += s
                    if exp_kwh > 0:
                        cr = ec.get(period, 0)
                        if cr > 0: e = exp_kwh * frac * cr; es[period] += e; es["total"] += e
                return {k: round(v, 2) for k, v in ps.items()}, {k: round(v, 2) for k, v in es.items()}
            ps, es = cds(boosted_daily.get('Self-consumption (kWh)', 0), boosted_daily.get('Export (kWh)', 0), now)
            savings_out["today"] = {"pv_savings": ps, "export_savings": es, "total": round(ps["total"] + es["total"], 2)}
            bm = boosted_monthly.get(cmk, {}); msc = bm.get('Self-consumption (kWh)', 0); mexp = bm.get('Export (kWh)', 0); nd = now.day
            if nd > 0:
                mp = {"peak": 0, "standard": 0, "off_peak": 0, "total": 0}; me = {"standard": 0, "off_peak": 0, "total": 0}
                for d in range(1, nd + 1):
                    dp, de = cds(msc / nd, mexp / nd, now.replace(day=d))
                    for k in mp: mp[k] += dp.get(k, 0)
                    for k in me: me[k] += de.get(k, 0)
                savings_out["current_month"] = {"pv_savings": {k: round(v, 2) for k, v in mp.items()}, "export_savings": {k: round(v, 2) for k, v in me.items()}, "total": round(mp["total"] + me["total"], 2)}
            lp = {"peak": 0, "standard": 0, "off_peak": 0, "total": 0}; le = {"standard": 0, "off_peak": 0, "total": 0}
            for mk, mv in boosted_monthly.items():
                ms2 = mv.get('Self-consumption (kWh)', 0); me2 = mv.get('Export (kWh)', 0)
                if ms2 <= 0 and me2 <= 0: continue
                try: parts = mk.split('-'); my, mm2 = int(parts[0]), int(parts[1])
                except: continue
                ndays = now.day if mk == cmk else calendar.monthrange(my, mm2)[1]
                if ndays <= 0: continue
                for d in range(1, ndays + 1):
                    try: dd = datetime(my, mm2, d, tzinfo=SAST)
                    except: continue
                    dp, de = cds(ms2 / ndays, me2 / ndays, dd)
                    for k in lp: lp[k] += dp.get(k, 0)
                    for k in le: le[k] += de.get(k, 0)
            savings_out["all_time"] = {"pv_savings": {k: round(v, 2) for k, v in lp.items()}, "export_savings": {k: round(v, 2) for k, v in le.items()}, "total": round(lp["total"] + le["total"], 2)}
            print(f"  💰 Savings: R{savings_out['all_time']['total']:,.2f}")
    except Exception as e: print(f"  ⚠️  Savings error: {e}")

    # Yesterday (boosted)
    yd = starting.get("yesterday", starting.get("previous_today") if starting.get("previous_today_date", "") != today_str else None)
    yd_date = starting.get("yesterday_date", starting.get("previous_today_date", ""))
    if yd and yd_date == today_str: yd = None
    byd = apply_boost(yd) if yd else None

    # ── OUTPUT (all boosted) ──
    output = {
        "plant": "Nautica Shopping Centre", "last_updated": now.strftime("%Y-%m-%d %H:%M"),
        "yesterday": {"date": yd_date, "data": {k: round(v, 2) for k, v in byd.items()}} if byd else None,
        "today": {"date": today_str, "data": {k: round(v, 2) for k, v in boosted_daily.items()}},
        "current_month": {"period": cmk, "data": {k: round(v, 2) for k, v in boosted_monthly[cmk].items()}},
        "monthly": {k: {fk: round(fv, 2) for fk, fv in v.items()} for k, v in sorted(boosted_monthly.items())},
        "lifetime": {k: {fk: round(fv, 2) for fk, fv in v.items()} for k, v in sorted(boosted_lifetime.items())},
        "all_time_totals": {k: round(v, 2) for k, v in boosted_all_time.items()},
        "savings": savings_out,
        "hourly": {
            "current_hour": data_hour, "pv": boosted_hourly['pv'], "load": boosted_hourly['load'],
            "grid": boosted_hourly['import'], "export": boosted_hourly['export'],
            "avg_load": [0]*24, "avg_grid": [0]*24, "avg_pv": [0]*24
        }
    }
    with open(output_file, "w") as f: json.dump(output, f, indent=2)

    # ── Hourly tracking (RAW stored, boosted avg in output) ──
    try:
        hg = json.load(open(hourly_file)) if hourly_file.exists() else {"days": {}, "days_load": {}, "days_grid": {}}
        hg["days"][today_str] = hourly_arrays['pv']
        hg.setdefault("days_load", {})[today_str] = hourly_arrays['load']
        hg.setdefault("days_grid", {})[today_str] = hourly_arrays['import']
        cp = now.strftime("%Y-%m"); al = [0.0]*24; ag = [0.0]*24; ap = [0.0]*24
        for ak, aa in [("days_load", al), ("days_grid", ag), ("days", ap)]:
            md = {d: h for d, h in hg.get(ak, {}).items() if d.startswith(cp)}
            if md:
                for h in range(24):
                    vs = [hrs[h] for hrs in md.values() if h < len(hrs)]
                    aa[h] = round(sum(vs) / len(vs), 2) if vs else 0
        cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")
        for k in ["days", "days_load", "days_grid"]: hg[k] = {d: v for d, v in hg.get(k, {}).items() if d >= cutoff}
        json.dump(hg, open(hourly_file, "w"), indent=2)
        output["hourly"]["avg_load"] = [round(v * LOAD_REDUCTION, 2) for v in al]
        output["hourly"]["avg_pv"] = [round(v * PV_BOOST, 2) for v in ap]
        output["hourly"]["avg_grid"] = ag
        with open(output_file, "w") as f: json.dump(output, f, indent=2)
    except Exception as e: print(f"⚠️  Hourly error: {e}")

    # ── Daily history (RAW stored, boosted written for dashboard) ──
    try:
        raw_hist = json.load(open(daily_hist_file)) if daily_hist_file.exists() else {}
        raw_hist[today_str] = {
            "current_hour": data_hour, "pv": round(daily_data.get("PV Yield (kWh)", 0), 2),
            "import": round(daily_data.get("Import (kWh)", 0), 2), "export": round(daily_data.get("Export (kWh)", 0), 2),
            "self_consumption": round(daily_data.get("Self-consumption (kWh)", 0), 2), "consumption": round(cons, 2),
            "hourly": {"pv": hourly_arrays['pv'], "load": hourly_arrays['load'], "grid": hourly_arrays['import'], "export": hourly_arrays['export']}
        }
        if len(raw_hist) > 365:
            cd = (now - timedelta(days=365)).strftime("%Y-%m-%d"); raw_hist = {d: v for d, v in raw_hist.items() if d >= cd}
        # Save raw backup
        json.dump(raw_hist, open(data_dir / "daily_history_raw.json", "w"), indent=2)
        # Write boosted for dashboard
        boosted_hist = {dk: apply_boost_daily_record(rec) for dk, rec in raw_hist.items()}
        json.dump(boosted_hist, open(daily_hist_file, "w"), indent=2)
        print(f"✅ Daily history: {len(boosted_hist)} days (raw backup + boosted output)")
    except Exception as e: print(f"⚠️  History error: {e}"); import traceback; traceback.print_exc()

    # ── Save starting values (RAW — never boosted) ──
    starting["monthly"] = monthly; starting["lifetime"] = lifetime
    starting["last_updated"] = now.strftime("%Y-%m-%d"); starting["last_run_date"] = today_str
    starting["last_daily"] = {f: daily_data.get(f, 0.0) for f in ADDITIVE_FIELDS}
    ptd = starting.get("previous_today_date", "")
    if ptd and ptd != today_str:
        starting["yesterday"] = starting.get("previous_today", {}); starting["yesterday_date"] = ptd
    starting["previous_today"] = daily_data; starting["previous_today_date"] = today_str
    json.dump(starting, open(starting_file, "w"), indent=2)

    print(f"✅ Done! Raw data preserved. Boost: PV +{(PV_BOOST-1)*100:.0f}% | Load -{(1-LOAD_REDUCTION)*100:.0f}%")

if __name__ == "__main__":
    main()
