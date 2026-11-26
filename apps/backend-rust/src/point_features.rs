//! Original information-daily-v3 research features. Independent from paper allocation.
use crate::{
    Result,
    calendar::{Calendar, cutoff_for},
    canonical,
    financials::{self, date, number, timestamp},
    invalid, monitoring,
    statistics::{mean, std},
};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::PgConnection;
use std::collections::{BTreeMap, BTreeSet};
pub const VERSION: &str = "information-daily-v3";
pub const NAMES: [&str; 36] = [
    "return_1",
    "return_5",
    "return_20",
    "return_60",
    "volatility_20",
    "atr_ratio_14",
    "relative_volume_20",
    "spy_return_5",
    "spy_volatility_20",
    "excess_return_20",
    "overnight_gap",
    "gap_mean_20",
    "volatility_ratio",
    "spy_return_60",
    "xlk_return_5",
    "xlk_return_20",
    "xli_return_5",
    "xli_return_20",
    "sector_excess_5",
    "counterpart_return_5",
    "counterpart_return_20",
    "counterpart_volatility_20",
    "news_count_1d_log",
    "news_count_5d_log",
    "unusual_news_activity",
    "days_since_news",
    "revenue_yoy",
    "operating_margin",
    "operating_margin_yoy_change",
    "cash_flow_yoy_change",
    "days_since_filing",
    "earnings_filing_last_7d",
    "guidance_direction_30d",
    "contracts_30d",
    "acquisitions_30d",
    "official_announcements_7d",
];
pub fn columns(group: &str) -> Result<Vec<usize>> {
    Ok(match group {
        "price" => (0..14).collect(),
        "market" => (0..22).collect(),
        "news" => (0..14).chain(22..26).collect(),
        "sec" => (0..14).chain(26..32).collect(),
        "ir" => (0..14).chain(32..36).collect(),
        "all" => (0..32).collect(),
        _ => return Err(invalid("Unknown information feature group")),
    })
}
#[derive(Clone, Default, Deserialize)]
pub struct Context {
    pub streams: BTreeMap<String, Vec<Value>>,
    pub information: Vec<Value>,
    pub coverage: Vec<Value>,
    pub facts: Vec<Value>,
    pub events: Vec<Value>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Row {
    pub date: NaiveDate,
    pub index: usize,
    #[serde(serialize_with = "serialize_features")]
    pub x: Vec<Option<f64>>,
    pub lineage: Value,
}
fn encoded_values(values: &[Option<f64>], days_column: usize) -> Result<Vec<Value>> {
    values
        .iter()
        .enumerate()
        .map(|(i, value)| {
            Ok(match value {
                Some(value) if !value.is_finite() => {
                    return Err(invalid("Nonfinite research feature"));
                }
                Some(value) if i == days_column && *value == 365.0 => json!(365),
                value => json!(value),
            })
        })
        .collect()
}
fn serialize_features<S: serde::Serializer>(
    values: &[Option<f64>],
    serializer: S,
) -> std::result::Result<S::Ok, S::Error> {
    encoded_values(values, 25)
        .map_err(serde::ser::Error::custom)?
        .serialize(serializer)
}
impl Row {
    pub fn payload(&self, symbol: &str, mode: &str) -> Result<Value> {
        if self.x.iter().flatten().any(|v| !v.is_finite()) {
            return Err(invalid("Nonfinite research feature"));
        }
        Ok(
            json!({"symbol":symbol,"date":self.date,"version":VERSION,"mode":mode,"x":encoded_values(&self.x,25)?,"lineage":self.lineage}),
        )
    }
    pub fn identity(&self, symbol: &str, mode: &str) -> Result<String> {
        canonical::digest(&self.payload(symbol, mode)?)
    }
}
pub fn information_at(
    rows: &[Value],
    provider: &str,
    symbol: &str,
    cutoff: DateTime<Utc>,
    observed: bool,
) -> Result<Vec<Value>> {
    let mut indices = BTreeMap::new();
    let mut selected: Vec<Value> = Vec::new();
    for row in rows {
        if row["provider"] != provider
            || !row["symbols"]
                .as_array()
                .is_some_and(|s| s.iter().any(|v| v == symbol))
        {
            continue;
        }
        if timestamp(&row["available_at"])? > cutoff
            || timestamp(&row["created_at"])? > cutoff
            || (observed && timestamp(&row["acquired_at"])? > cutoff)
        {
            continue;
        }
        let key = row["external_id"]
            .as_str()
            .ok_or_else(|| invalid("Missing information identity"))?
            .to_owned();
        if let Some(index) = indices.get(&key).copied() {
            let old: &Value = &selected[index];
            if timestamp(&row["updated_at"])? > timestamp(&old["updated_at"])? {
                selected[index] = row.clone();
            }
        } else {
            indices.insert(key, selected.len());
            selected.push(row.clone());
        }
    }
    Ok(selected)
}
fn days_between(later: DateTime<Utc>, earlier: DateTime<Utc>) -> Result<f64> {
    Ok((later - earlier)
        .num_microseconds()
        .ok_or_else(|| invalid("Information duration outside range"))? as f64
        / 1_000_000.0
        / 86400.0)
}
struct FinancialValues {
    values: Vec<Option<f64>>,
    fact_ids: Vec<Value>,
    filing_ids: Vec<Value>,
}
fn financial_values(
    facts: &[Value],
    events: &[Value],
    cutoff: DateTime<Utc>,
    observed: bool,
) -> Result<FinancialValues> {
    let selected = financials::select_versions(facts, cutoff, observed)?;
    let mut quarters = Vec::new();
    let mut ocfs = Vec::new();
    for row in &selected {
        let kind = financials::period_kind(row)?;
        if kind == "quarter" {
            quarters.push(row);
        }
        if row["metric"] == "operating_cash_flow"
            && matches!(kind, "annual" | "quarter" | "year_to_date")
        {
            ocfs.push(row);
        }
    }
    quarters.sort_by_key(|r| std::cmp::Reverse(r["period_end"].as_str().unwrap_or_default()));
    ocfs.sort_by_key(|r| std::cmp::Reverse(r["period_end"].as_str().unwrap_or_default()));
    let revenue = quarters.iter().copied().find(|r| r["metric"] == "revenue");
    let mut previous = None;
    if let Some(revenue) = revenue {
        for row in &quarters {
            if row["metric"] == "revenue"
                && (330..=400).contains(
                    &(date(&revenue["period_end"])? - date(&row["period_end"])?).num_days(),
                )
            {
                previous = Some(*row);
                break;
            }
        }
    }
    let growth = match (revenue, previous) {
        (Some(a), Some(b)) if number(&b["value"])? > 0.0 => {
            Some(number(&a["value"])? / number(&b["value"])? - 1.0)
        }
        _ => None,
    };
    let margin = |revenue: Option<&Value>| -> Result<Option<f64>> {
        if let Some(rev) = revenue {
            for op in &quarters {
                if op["metric"] == "operating_income"
                    && ["period_start", "period_end", "unit"]
                        .iter()
                        .all(|key| op[*key] == rev[*key])
                    && number(&rev["value"])? > 0.0
                {
                    return Ok(Some(number(&op["value"])? / number(&rev["value"])?));
                }
            }
        }
        Ok(None)
    };
    let current_margin = margin(revenue)?;
    let old_margin = margin(previous)?;
    let mut change = None;
    if let Some(current) = ocfs.first() {
        for old in &ocfs {
            let days = (date(&current["period_end"])? - date(&old["period_end"])?).num_days();
            let a = (date(&current["period_end"])? - date(&current["period_start"])?).num_days();
            let b = (date(&old["period_end"])? - date(&old["period_start"])?).num_days();
            if (330..=400).contains(&days) && (a - b).abs() <= 15 {
                if number(&old["value"])? != 0.0 {
                    change = Some(
                        (number(&current["value"])? - number(&old["value"])?)
                            / number(&old["value"])?.abs(),
                    );
                }
                break;
            }
        }
    }
    let mut eligible = Vec::new();
    for event in events {
        if timestamp(&event["published_at"])? <= cutoff
            && (!observed || timestamp(&event["acquired_at"])? <= cutoff)
        {
            eligible.push(event);
        }
    }
    let mut latest = None;
    let mut earnings = 0;
    for event in &eligible {
        let published = timestamp(&event["published_at"])?;
        latest = Some(latest.map_or(published, |old: DateTime<Utc>| old.max(published)));
        if event["items"].as_str().is_some_and(|s| s.contains("2.02"))
            && event["form"].as_str().is_some_and(|s| s.starts_with("8-K"))
            && published >= cutoff - Duration::days(7)
        {
            earnings += 1;
        }
    }
    Ok(FinancialValues {
        values: vec![
            growth,
            current_margin,
            current_margin.zip(old_margin).map(|(a, b)| a - b),
            change,
            latest.map(|t| days_between(cutoff, t)).transpose()?,
            if eligible.is_empty() {
                None
            } else {
                Some(f64::from(earnings))
            },
        ],
        fact_ids: selected.iter().map(|r| r["id"].clone()).collect(),
        filing_ids: eligible.iter().map(|r| r["id"].clone()).collect(),
    })
}
pub fn information_values(
    symbol: &str,
    cutoff: DateTime<Utc>,
    context: &Context,
    observed: bool,
) -> Result<(Vec<Value>, Value)> {
    let (values, lineage) = information_parts(
        symbol,
        cutoff,
        context,
        observed,
        &context.facts,
        &context.events,
    )?;
    Ok((encoded_values(&values, 3)?, lineage))
}
fn information_parts(
    symbol: &str,
    cutoff: DateTime<Utc>,
    context: &Context,
    observed: bool,
    facts: &[Value],
    events: &[Value],
) -> Result<(Vec<Option<f64>>, Value)> {
    let news_cutoff = cutoff - Duration::hours(1);
    let mut covered = false;
    for c in &context.coverage {
        if c["provider"] == "alpaca_news"
            && c["status"] == "complete"
            && c["symbols"]
                .as_array()
                .is_some_and(|s| s.iter().any(|v| v == symbol))
            && timestamp(&c["started_at"])? <= news_cutoff - Duration::days(25)
            && timestamp(&c["ended_at"])? >= news_cutoff
            && (!observed || timestamp(&c["acquired_at"])? <= cutoff)
        {
            covered = true;
            break;
        }
    }
    let mut news = information_at(
        &context.information,
        "alpaca_news",
        symbol,
        news_cutoff,
        false,
    )?;
    if observed {
        let mut eligible = Vec::new();
        for row in news {
            if timestamp(&row["acquired_at"])? <= cutoff {
                eligible.push(row);
            }
        }
        news = eligible;
    }
    let mut counts = [0_u32; 3];
    let mut latest = None;
    let mut news_ids = Vec::new();
    for row in &news {
        let created = timestamp(&row["created_at"])?;
        latest = Some(latest.map_or(created, |old: DateTime<Utc>| old.max(created)));
        for (i, days) in [1, 5, 25].into_iter().enumerate() {
            if created > news_cutoff - Duration::days(days) {
                counts[i] += 1;
            }
        }
        if created > news_cutoff - Duration::days(25) {
            news_ids.push(row["id"].clone());
        }
    }
    let mut values = if covered {
        vec![
            Some(f64::from(counts[0]).ln_1p()),
            Some(f64::from(counts[1]).ln_1p()),
            Some(f64::from(counts[0]) / (1.0 + f64::from(counts[2] - counts[1]) / 20.0)),
            Some(
                latest
                    .map(|t| days_between(news_cutoff, t))
                    .transpose()?
                    .unwrap_or(365.0)
                    .min(365.0),
            ),
        ]
    } else {
        vec![None; 4]
    };
    let FinancialValues {
        values: sec,
        fact_ids,
        filing_ids,
    } = financial_values(facts, events, cutoff, observed)?;
    values.extend(sec);
    let ir = information_at(
        &context.information,
        "official_ir",
        symbol,
        cutoff,
        observed,
    )?;
    let mut ir_ids = Vec::new();
    let mut directions = Vec::new();
    let (mut contracts, mut acquisitions, mut announcements) = (0, 0, 0);
    for row in &ir {
        if timestamp(&row["created_at"])? > cutoff - Duration::days(30) {
            ir_ids.push(row["id"].clone());
            let flags = &row["payload"]["flags"];
            if !flags["guidance_direction"].is_null() {
                directions.push(number(&flags["guidance_direction"])?);
            }
            contracts += i32::from(flags["contract"].as_bool().unwrap_or(false));
            acquisitions += i32::from(flags["acquisition"].as_bool().unwrap_or(false));
            announcements +=
                i32::from(timestamp(&row["available_at"])? > cutoff - Duration::days(7));
        }
    }
    values.extend(if ir.is_empty() {
        vec![None; 4]
    } else {
        vec![
            if directions.is_empty() {
                None
            } else {
                Some(directions.iter().sum())
            },
            Some(f64::from(contracts)),
            Some(f64::from(acquisitions)),
            Some(f64::from(announcements)),
        ]
    });
    Ok((
        values,
        json!({"news_ids":news_ids,"news_coverage":covered,"fact_ids":fact_ids,"filing_ids":filing_ids,"ir_ids":ir_ids,"news_lag_minutes":60,"historical_news":"Current archive censored by version availability, not original-vintage completeness"}),
    ))
}
fn log_returns(values: &[f64]) -> Vec<f64> {
    values.windows(2).map(|w| w[1].ln() - w[0].ln()).collect()
}
pub fn build_features(
    calendar: &Calendar,
    context: &Context,
    symbol: &str,
    observed: bool,
    latest_only: bool,
) -> Result<Vec<Row>> {
    for rows in context.streams.values() {
        if !rows.is_empty() && !monitoring::continuity(calendar, rows)?.0 {
            return Err(invalid(
                "A context price stream has missing sessions or mixed adjustment policy",
            ));
        }
    }
    let own = context
        .streams
        .get(symbol)
        .ok_or_else(|| invalid("Missing own price stream"))?;
    let spy = context
        .streams
        .get("SPY")
        .ok_or_else(|| invalid("Missing SPY price stream"))?;
    let basis: BTreeSet<_> = own
        .iter()
        .chain(spy)
        .map(|r| {
            (
                r["feed"].to_string(),
                r["adjustment"].to_string(),
                r["adjustment_asof"].to_string(),
            )
        })
        .collect();
    if basis.len() != 1 {
        return Err(invalid(
            "Stock and benchmark must share feed and adjustment snapshot",
        ));
    }
    for rows in [own, spy] {
        let (valid, reason) = monitoring::continuity(calendar, rows)?;
        if !valid {
            return Err(invalid(reason.unwrap_or("Invalid price stream")));
        }
    }
    let maps: BTreeMap<_, BTreeMap<_, _>> = context
        .streams
        .iter()
        .map(|(s, rows)| {
            Ok((
                s.clone(),
                rows.iter()
                    .enumerate()
                    .map(|(i, r)| Ok((date(&r["session_date"])?, i)))
                    .collect::<Result<_>>()?,
            ))
        })
        .collect::<Result<_>>()?;
    let own_close: Vec<f64> = own
        .iter()
        .map(|r| number(&r["close"]))
        .collect::<Result<_>>()?;
    let spy_close: Vec<f64> = spy
        .iter()
        .map(|r| number(&r["close"]))
        .collect::<Result<_>>()?;
    let volume: Vec<f64> = own
        .iter()
        .map(|r| number(&r["volume"]))
        .collect::<Result<_>>()?;
    let mut panel = Vec::new();
    for i in 60..own.len() {
        let day = date(&own[i]["session_date"])?;
        let Some(&j) = maps["SPY"].get(&day).filter(|j| **j >= 60) else {
            continue;
        };
        let ret = |n| own_close[i] / own_close[i - n] - 1.0;
        let sr = |n| spy_close[j] / spy_close[j - n] - 1.0;
        let fast = std(&log_returns(&own_close[i - 20..=i]));
        let sv = std(&log_returns(&spy_close[j - 20..=j]));
        let mut tr = Vec::new();
        for k in i - 13..=i {
            let high = number(&own[k]["high"])?;
            let low = number(&own[k]["low"])?;
            tr.push(
                (high - low)
                    .max((high - own_close[k - 1]).abs())
                    .max((low - own_close[k - 1]).abs()),
            );
        }
        let average_volume = mean(&volume[i - 20..i]);
        if average_volume <= 0.0 {
            continue;
        }
        let mut x = vec![
            ret(1),
            ret(5),
            ret(20),
            ret(60),
            fast,
            mean(&tr) / own_close[i],
            volume[i] / average_volume,
            sr(5),
            sv,
            ret(20) - sr(20),
        ];
        if x.iter().any(|v| !v.is_finite()) {
            continue;
        }
        let gaps: Vec<f64> = (i - 19..=i)
            .map(|k| Ok(number(&own[k]["open"])? / own_close[k - 1] - 1.0))
            .collect::<Result<_>>()?;
        let slow = std(&log_returns(&own_close[i - 60..=i])).max(1e-9);
        x.extend([
            gaps[19],
            mean(&gaps),
            x[4] / slow,
            spy_close[j] / spy_close[j - 60] - 1.0,
        ]);
        panel.push(Row {
            date: day,
            index: i,
            x: x.into_iter().map(Some).collect(),
            lineage: Value::Null,
        });
    }
    if latest_only && panel.len() > 1 {
        panel.drain(..panel.len() - 1);
    }
    let facts: Vec<Value> = context
        .facts
        .iter()
        .filter(|r| r["symbol"] == symbol)
        .cloned()
        .collect();
    let events: Vec<Value> = context
        .events
        .iter()
        .filter(|r| r["symbol"] == symbol)
        .cloned()
        .collect();
    for row in &mut panel {
        let ret = |s: &str, n: usize| -> Result<Option<f64>> {
            let Some(&i) = maps
                .get(s)
                .and_then(|m| m.get(&row.date))
                .filter(|i| **i >= n)
            else {
                return Ok(None);
            };
            let stream = &context.streams[s];
            Ok(Some(
                number(&stream[i]["close"])? / number(&stream[i - n]["close"])? - 1.0,
            ))
        };
        let values = [
            ret("XLK", 5)?,
            ret("XLK", 20)?,
            ret("XLI", 5)?,
            ret("XLI", 20)?,
        ];
        let relative = values[0]
            .zip(values[2])
            .map(|(a, b)| row.x[1].expect("price return") - (a + b) / 2.0);
        let peer = if symbol == "MSI" { "AXON" } else { "MSI" };
        let vol = if let Some(&i) = maps
            .get(peer)
            .and_then(|m| m.get(&row.date))
            .filter(|i| **i >= 20)
        {
            let closes: Vec<f64> = context.streams[peer][i - 20..=i]
                .iter()
                .map(|r| number(&r["close"]))
                .collect::<Result<_>>()?;
            Some(std(&log_returns(&closes)))
        } else {
            None
        };
        row.x.extend(values);
        row.x.extend([relative, ret(peer, 5)?, ret(peer, 20)?, vol]);
        let (extra, mut lineage) = information_parts(
            symbol,
            cutoff_for(row.date)?,
            context,
            observed,
            &facts,
            &events,
        )?;
        row.x.extend(extra);
        lineage["price_bar_ids"] = json!(
            own[row.index - 60..=row.index]
                .iter()
                .map(|r| r["id"].clone())
                .collect::<Vec<_>>()
        );
        let mut market = serde_json::Map::new();
        for (s, rows) in &context.streams {
            let ids = if let Some(&index) = maps[s].get(&row.date) {
                rows[index.saturating_sub(60)..=index]
                    .iter()
                    .map(|r| r["id"].clone())
                    .collect::<Vec<_>>()
            } else {
                Vec::new()
            };
            market.insert(s.clone(), json!(ids));
        }
        lineage["market_bar_ids"] = Value::Object(market);
        row.lineage = lineage;
        row.payload(
            symbol,
            if observed {
                "prospective"
            } else {
                "historical"
            },
        )?;
    }
    Ok(panel)
}
pub async fn load_context(
    connection: &mut PgConnection,
    feed: &str,
    cutoff: Option<DateTime<Utc>>,
    observed: bool,
) -> Result<Context> {
    let mut streams = BTreeMap::new();
    for symbol in ["MSI", "AXON", "SPY", "XLK", "XLI"] {
        let rows:Vec<Value>=sqlx::query_scalar("WITH basis AS (SELECT max(adjustment_asof) d FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split' AND ($3::timestamptz IS NULL OR available_at<=$3) AND ($4::timestamptz IS NULL OR acquired_at<=$4)) SELECT to_jsonb(r)||jsonb_build_object('open',r.open::text,'close',r.close::text) FROM (SELECT DISTINCT ON(session_date) * FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split' AND ($3::timestamptz IS NULL OR available_at<=$3) AND ($4::timestamptz IS NULL OR acquired_at<=$4) AND adjustment_asof=(SELECT d FROM basis) ORDER BY session_date,acquired_at DESC,id DESC) r").bind(symbol).bind(feed).bind(cutoff).bind(if observed{cutoff}else{None}).fetch_all(&mut *connection).await?;
        streams.insert(symbol.into(), rows);
    }
    let information = sqlx::query_scalar("SELECT to_jsonb(r) FROM information_versions r")
        .fetch_all(&mut *connection)
        .await?;
    let coverage = sqlx::query_scalar("SELECT to_jsonb(r) FROM information_fetches r")
        .fetch_all(&mut *connection)
        .await?;
    let facts = sqlx::query_scalar(
        "SELECT to_jsonb(r)||jsonb_build_object('value',r.value::text) FROM financial_facts r",
    )
    .fetch_all(&mut *connection)
    .await?;
    let events = sqlx::query_scalar("SELECT to_jsonb(r) FROM filing_events r")
        .fetch_all(connection)
        .await?;
    Ok(Context {
        streams,
        information,
        coverage,
        facts,
        events,
    })
}
pub async fn persist_panel(
    connection: &mut PgConnection,
    symbol: &str,
    panel: &[Row],
    mode: &str,
) -> Result<Vec<String>> {
    let mut ids = Vec::new();
    for row in panel {
        let id = row.identity(symbol, mode)?;
        sqlx::query("INSERT INTO feature_snapshots(id,symbol,origin_session,cutoff,version,mode,values,lineage) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING").bind(&id).bind(symbol).bind(row.date).bind(cutoff_for(row.date)?).bind(VERSION).bind(mode).bind(json!(encoded_values(&row.x,25)?)).bind(&row.lineage).execute(&mut *connection).await?;
        ids.push(id);
    }
    Ok(ids)
}
