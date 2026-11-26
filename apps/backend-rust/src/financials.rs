//! Period-compatible, versioned GAAP calculations. No estimates or substitutions.
use crate::{Result, invalid};
use chrono::{DateTime, NaiveDate, Utc};
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;

pub(crate) fn number(value: &Value) -> Result<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.parse().ok()))
        .filter(|v| v.is_finite())
        .ok_or_else(|| invalid("Invalid numerical observation"))
}
pub(crate) fn date(value: &Value) -> Result<NaiveDate> {
    value
        .as_str()
        .ok_or_else(|| invalid("Missing date"))?
        .parse()
        .map_err(|_| invalid("Invalid date"))
}
pub(crate) fn timestamp(value: &Value) -> Result<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value.as_str().ok_or_else(|| invalid("Missing timestamp"))?)
        .map(|v| v.with_timezone(&Utc))
        .map_err(|_| invalid("Invalid aware timestamp"))
}
pub fn period_kind(row: &Value) -> Result<&'static str> {
    if row["period_start"].is_null() {
        return Ok("instant");
    }
    let days = (date(&row["period_end"])? - date(&row["period_start"])?).num_days() + 1;
    Ok(match days {
        70..=105 => "quarter",
        350..=380 => "annual",
        150..=300 => "year_to_date",
        _ => "other",
    })
}
pub fn select_versions(
    rows: &[Value],
    asof: DateTime<Utc>,
    observed_only: bool,
) -> Result<Vec<Value>> {
    let metadata: Value = serde_json::from_str(include_str!("../assets/financial-tags.json"))?;
    let mut indices = BTreeMap::new();
    let mut selected: Vec<(DateTime<Utc>, usize, String, Value)> = Vec::new();
    for row in rows {
        let Some(metric) = row["metric"].as_str() else {
            continue;
        };
        let Some(tags) = metadata["tags"][metric].as_array() else {
            continue;
        };
        let Some(priority) = tags.iter().position(|v| v == &row["tag"]) else {
            continue;
        };
        let available = timestamp(&row["available_at"])?;
        if available > asof || (observed_only && timestamp(&row["acquired_at"])? > asof) {
            continue;
        }
        let start = if row["period_start"].is_null() {
            None
        } else {
            Some(date(&row["period_start"])?)
        };
        let key = (metric.to_owned(), start, date(&row["period_end"])?);
        let accession = row["accession"]
            .as_str()
            .ok_or_else(|| invalid("Missing accession"))?
            .to_owned();
        if let Some(index) = indices.get(&key).copied() {
            let previous: &(DateTime<Utc>, usize, String, Value) = &selected[index];
            if (available, std::cmp::Reverse(priority), accession.as_str())
                > (
                    previous.0,
                    std::cmp::Reverse(previous.1),
                    previous.2.as_str(),
                )
            {
                selected[index] = (available, priority, accession, row.clone());
            }
        } else {
            indices.insert(key, selected.len());
            selected.push((available, priority, accession, row.clone()));
        }
    }
    Ok(selected.into_iter().map(|(_, _, _, row)| row).collect())
}
fn reference(row: &Value) -> Value {
    Value::Object(
        [
            "id",
            "tag",
            "value",
            "unit",
            "period_start",
            "period_end",
            "accession",
            "available_at",
            "source_url",
        ]
        .into_iter()
        .map(|k| (k.into(), row[k].clone()))
        .collect(),
    )
}
fn metric(label: &str, row: &Value) -> Result<Value> {
    Ok(
        json!({"label":label,"value":number(&row["value"])?,"unit":row["unit"],"period_kind":period_kind(row)?,"period_start":row["period_start"],"period_end":row["period_end"],"source_url":row["source_url"],"tag":row["tag"],"accession":row["accession"],"available_at":row["available_at"],"inputs":[reference(row)],"unavailable_reason":null}),
    )
}
fn missing(label: &str, reason: &str) -> Value {
    json!({"label":label,"value":null,"unit":null,"unavailable_reason":reason})
}
fn compatible(a: &Value, b: &Value) -> bool {
    ["period_start", "period_end", "unit"]
        .iter()
        .all(|k| a[k] == b[k])
}
pub fn calculate_metrics(rows: &[Value], asof: DateTime<Utc>) -> Result<Value> {
    let facts = select_versions(rows, asof, false)?;
    let mut history = Vec::new();
    for row in facts {
        history.push((
            date(&row["period_end"])?,
            period_kind(&row)? == "quarter",
            timestamp(&row["available_at"])?,
            row,
        ));
    }
    history.sort_by(|a, b| (b.0, b.1, b.2).cmp(&(a.0, a.1, a.2)));
    let history: Vec<Value> = history.into_iter().map(|(_, _, _, row)| row).collect();
    let labels = [
        ("revenue", "Revenue"),
        ("net_income", "GAAP net income"),
        ("eps", "Diluted EPS"),
        ("operating_income", "Operating income"),
        ("operating_cash_flow", "Operating cash flow"),
        ("capex", "Reported cash capital investment"),
        ("cash", "Cash and equivalents"),
        ("diluted_shares", "Diluted shares"),
    ];
    let mut output = Map::new();
    for (name, label) in labels {
        let mut matched = None;
        for row in &history {
            if row["metric"] == name {
                let kind = period_kind(row)?;
                if matches!(kind, "quarter" | "annual" | "instant")
                    || (matches!(name, "operating_cash_flow" | "capex") && kind == "year_to_date")
                {
                    matched = Some(row);
                    break;
                }
            }
        }
        output.insert(
            name.into(),
            match matched {
                Some(row) => metric(label, row)?,
                None => missing(
                    label,
                    "No supported USD GAAP fact with a complete quarter or fiscal year",
                ),
            },
        );
    }
    for (name, label, a_key, b_key, divide) in [
        (
            "operating_margin",
            "Operating margin",
            "operating_income",
            "revenue",
            true,
        ),
        (
            "free_cash_flow",
            "Free cash flow",
            "operating_cash_flow",
            "capex",
            false,
        ),
    ] {
        let mut pair = None;
        'outer: for a in &history {
            if a["metric"] == a_key
                && matches!(period_kind(a)?, "quarter" | "annual" | "year_to_date")
            {
                for b in &history {
                    if b["metric"] == b_key && compatible(a, b) {
                        pair = Some((a, b));
                        break 'outer;
                    }
                }
            }
        }
        let value = if let Some((a, b)) = pair {
            let denominator = number(&b["value"])?;
            if divide && denominator <= 0.0 {
                missing(label, "Revenue must be positive")
            } else {
                let mut value = metric(label, a)?;
                value["value"] = json!(if divide {
                    number(&a["value"])? / denominator
                } else {
                    number(&a["value"])? - denominator
                });
                value["unit"] = json!(if divide { "ratio" } else { "USD" });
                value["formula"] = json!(if divide {
                    "Operating income / revenue"
                } else {
                    "Operating cash flow \u{00e2}\u{02c6}\u{2019} cash capital expenditure"
                });
                value["inputs"] = json!([reference(a), reference(b)]);
                value
            }
        } else {
            missing(
                label,
                "Matching reporting periods and units are required; quarters and year-to-date values are not mixed",
            )
        };
        output.insert(name.into(), value);
    }
    let pair = history
        .iter()
        .filter(|r| r["metric"] == "current_debt")
        .find_map(|a| {
            history
                .iter()
                .find(|b| b["metric"] == "long_term_debt" && compatible(a, b))
                .map(|b| (a, b))
        });
    let debt = if let Some((a, b)) = pair {
        let mut value = metric("Reported current + long-term debt", a)?;
        value["value"] = json!(number(&a["value"])? + number(&b["value"])?);
        value["inputs"] = json!([reference(a), reference(b)]);
        value["formula"] =
            json!("Reported current debt + reported noncurrent debt; leases depend on source tags");
        value
    } else {
        missing(
            "Debt",
            "Both current and noncurrent components for the same date are required",
        )
    };
    output.insert("debt".into(), debt);
    let quarters: Vec<_> = history
        .iter()
        .filter(|r| r["metric"] == "revenue")
        .filter(|r| matches!(period_kind(r), Ok("quarter")))
        .collect();
    let mut changes = Vec::new();
    for current in quarters.iter().take(4) {
        let end = date(&current["period_end"])?;
        let previous = quarters.iter().find(|r| {
            date(&r["period_end"]).is_ok_and(|d| (330..=400).contains(&(end - d).num_days()))
        });
        if let Some(previous) = previous {
            let value = number(&previous["value"])?;
            if value != 0.0 {
                changes.push(json!({"metric":"Revenue year-over-year","period_end":current["period_end"],"value":number(&current["value"])?/value-1.0,"inputs":[reference(current),reference(previous)]}));
            }
        }
    }
    let mut rendered = Vec::new();
    for row in history.iter().take(120) {
        let name = row["metric"]
            .as_str()
            .ok_or_else(|| invalid("Missing metric"))?;
        let label = labels
            .iter()
            .find(|(key, _)| *key == name)
            .map(|(_, v)| *v)
            .unwrap_or(name);
        let mut value = metric(label, row)?;
        value["metric"] = json!(name);
        rendered.push(value);
    }
    Ok(
        json!({"version":"gaap-metrics-v3","as_of":crate::engine::stamp(asof),"metrics":output,"changes":changes,"history":rendered,"limitations":["GAAP only. No consensus estimates or non-GAAP substitutions. Capital investment can include software and intangible assets; inspect source tags.","Operating cash flow and capex can be year-to-date; their periods are shown explicitly.","Historical availability reconstructed from filing metadata is not observed delivery latency."]}),
    )
}
