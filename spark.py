"""
Pandas-powered analytics for crmpy.
"""

import sqlite3
from db import DB_PATH


# ── DB helpers ────────────────────────────────────────────────────────────────

def _df(query: str):
    import pandas as pd
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn)


# ── analytics ─────────────────────────────────────────────────────────────────

def pipeline_analytics() -> dict:
    import pandas as pd
    deals    = _df("SELECT * FROM deals")
    contacts = _df("SELECT * FROM contacts")

    total  = len(deals)
    won    = deals[deals.stage == "won"]
    lost   = deals[deals.stage == "lost"]
    closed = len(won) + len(lost)

    win_rate = round(len(won) / closed * 100, 1) if closed else 0
    avg_size = won["value"].mean() if len(won) else 0

    # velocity
    vel = None
    if len(won) and "closed_at" in won.columns:
        w = won.dropna(subset=["closed_at"]).copy()
        if len(w):
            w["days"] = (
                pd.to_datetime(w["closed_at"]) - pd.to_datetime(w["created_at"])
            ).dt.days
            vel = round(w["days"].mean(), 1)

    # monthly
    monthly = []
    if len(won) and "closed_at" in won.columns:
        w = won.dropna(subset=["closed_at"]).copy()
        w["month"] = pd.to_datetime(w["closed_at"]).dt.strftime("%Y-%m")
        grp = w.groupby("month").agg(revenue=("value", "sum"), deals=("id", "count")).reset_index()
        monthly = grp.to_dict("records")

    # funnel
    funnel = (
        deals.groupby("stage")
        .agg(count=("id", "count"), value=("value", "sum"))
        .reset_index()
        .to_dict("records")
    )

    # top companies
    open_deals = deals[~deals.stage.isin(["won", "lost"])].copy()
    top_companies = []
    if len(open_deals):
        merged = open_deals.merge(contacts[["id", "company"]], left_on="contact_id", right_on="id", suffixes=("", "_c"))
        merged = merged.dropna(subset=["company"])
        if len(merged):
            top_companies = (
                merged.groupby("company")
                .agg(pipeline=("value", "sum"), deals=("id_c", "count"))
                .reset_index()
                .sort_values("pipeline", ascending=False)
                .head(10)
                .to_dict("records")
            )

    return {
        "engine":        "pandas",
        "total_deals":   total,
        "win_rate":      win_rate,
        "avg_deal_size": round(avg_size, 2),
        "velocity_days": vel,
        "monthly":       monthly,
        "funnel":        funnel,
        "top_companies": top_companies,
    }


def contact_scores() -> list[dict]:
    deals      = _df("SELECT * FROM deals")
    activities = _df("SELECT * FROM activities")
    contacts   = _df("SELECT * FROM contacts")

    deal_agg = deals.groupby("contact_id").agg(
        deal_value=("value", "sum"),
        deal_count=("id", "count"),
        won_count=("stage", lambda s: (s == "won").sum()),
    ).reset_index()

    act_agg = (
        activities[activities.contact_id.notna()]
        .groupby("contact_id")
        .agg(activity_count=("id", "count"))
        .reset_index()
    )

    merged = (
        contacts
        .merge(deal_agg, left_on="id", right_on="contact_id", how="left")
        .merge(act_agg,  left_on="id", right_on="contact_id", how="left")
    )
    for col in ["deal_value", "deal_count", "won_count", "activity_count"]:
        merged[col] = merged[col].fillna(0)

    merged["score"] = (
        merged["deal_value"] / 1000 * 0.5
        + merged["won_count"] * 20
        + merged["activity_count"] * 2
        + merged["deal_count"] * 5
    )
    merged = merged.sort_values("score", ascending=False)

    return merged[["id", "first_name", "last_name", "company",
                   "deal_value", "deal_count", "won_count", "activity_count", "score"]].to_dict("records")


def import_contacts_csv(path: str) -> dict:
    import pandas as pd
    df      = pd.read_csv(path)
    total   = len(df)
    valid   = df.dropna(subset=["first_name", "last_name"])
    invalid = total - len(valid)
    valid   = valid.drop_duplicates(subset=["email"])

    existing = set(_df("SELECT email FROM contacts WHERE email IS NOT NULL")["email"].tolist())
    new_rows = valid[~valid["email"].isin(existing) | valid["email"].isna()]

    rows = [
        (r.get("first_name"), r.get("last_name"),
         r.get("email") if pd.notna(r.get("email")) else None,
         r.get("phone")   if pd.notna(r.get("phone"))  else None,
         r.get("company") if pd.notna(r.get("company")) else None)
        for _, r in new_rows.iterrows()
    ]

    inserted = 0
    with sqlite3.connect(DB_PATH) as conn:
        for vals in rows:
            try:
                conn.execute(
                    "INSERT INTO contacts (first_name, last_name, email, phone, company) VALUES (?,?,?,?,?)",
                    vals,
                )
                inserted += 1
            except Exception:
                pass
        conn.commit()
    return {"total": total, "invalid": invalid, "skipped": len(rows) - inserted, "inserted": inserted}


def export_contacts_csv(dest: str):
    _df("SELECT * FROM contacts").to_csv(dest, index=False)


def export_deals_csv(dest: str):
    _df("SELECT * FROM deals").to_csv(dest, index=False)
