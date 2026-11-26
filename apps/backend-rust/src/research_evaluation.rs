//! Existing technical-v1 historical protocol. No trading, model promotion or new search.
pub use crate::intraday::evaluation::simulate as simulate_intraday_portfolio;
use crate::{
    Result,
    calendar::Calendar,
    financials, invalid, monitoring,
    research_training::{Limits, fit_heads},
    statistics,
};
use chrono::{Datelike, NaiveDate};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub fn protocol() -> Value {
    json!({"version":"technical-v1","splits":[0.6,0.2,0.2],"purge_sessions":5,"bootstrap_samples":2000,"block_sessions":20,"confidence":0.9875,"primary_comparisons":4,"minimum_evaluation_dates":126,"feature_version":"technical-daily-v1","historical_not_prospective":true})
}
pub const FEATURES: [&str; 10] = [
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
];
#[derive(Clone, Debug)]
pub struct PanelRow {
    pub date: NaiveDate,
    pub index: usize,
    pub x: Vec<f64>,
}
fn numbers(rows: &[Value], key: &str) -> Result<Vec<f64>> {
    rows.iter().map(|r| financials::number(&r[key])).collect()
}
pub fn build_panel(calendar: &Calendar, bars: &[Value], spy: &[Value]) -> Result<Vec<PanelRow>> {
    for stream in [bars, spy] {
        let (valid, reason) = monitoring::continuity(calendar, stream)?;
        if !valid {
            return Err(invalid(reason.unwrap_or("Invalid market data")));
        }
    }
    if bars.len() > 20_000 || spy.len() > 20_000 {
        return Err(invalid("Historical evaluation resource boundary"));
    }
    let spy_at: BTreeMap<_, _> = spy
        .iter()
        .enumerate()
        .map(|(i, r)| Ok((financials::date(&r["session_date"])?, i)))
        .collect::<Result<_>>()?;
    let close = numbers(bars, "close")?;
    let volume = numbers(bars, "volume")?;
    let sc = numbers(spy, "close")?;
    let logs: Vec<_> = close.iter().map(|v| v.ln()).collect();
    let slogs: Vec<_> = sc.iter().map(|v| v.ln()).collect();
    let mut records = Vec::new();
    for i in 60..bars.len() {
        let date = financials::date(&bars[i]["session_date"])?;
        let Some(&j) = spy_at.get(&date).filter(|j| **j >= 60) else {
            continue;
        };
        let r = |n| close[i] / close[i - n] - 1.;
        let sr = |n| sc[j] / sc[j - n] - 1.;
        let ret: Vec<_> = logs[i - 20..=i].windows(2).map(|v| v[1] - v[0]).collect();
        let sret: Vec<_> = slogs[j - 20..=j].windows(2).map(|v| v[1] - v[0]).collect();
        let mut tr = Vec::with_capacity(14);
        for k in i - 13..=i {
            let h = financials::number(&bars[k]["high"])?;
            let l = financials::number(&bars[k]["low"])?;
            tr.push(
                (h - l)
                    .max((h - close[k - 1]).abs())
                    .max((l - close[k - 1]).abs()),
            );
        }
        let mean = statistics::mean(&volume[i - 20..i]);
        if mean <= 0. {
            continue;
        }
        let x = vec![
            r(1),
            r(5),
            r(20),
            r(60),
            statistics::std(&ret),
            statistics::mean(&tr) / close[i],
            volume[i] / mean,
            sr(5),
            statistics::std(&sret),
            r(20) - sr(20),
        ];
        if x.iter().all(|v| v.is_finite()) {
            records.push(PanelRow { date, index: i, x });
        }
    }
    Ok(records)
}
fn scores(y: &[f64], expected: &[f64], p: &[f64]) -> Result<Value> {
    let mut all = monitoring::metrics(y, expected, p, None)?;
    let object = all
        .as_object_mut()
        .ok_or_else(|| invalid("Metrics object missing"))?;
    Ok(
        json!({"mae":object.remove("mae"),"brier":object.remove("brier"),"log_loss":object.remove("log_loss"),"calibration":object.remove("calibration")}),
    )
}
#[derive(Clone)]
struct Row {
    date: NaiveDate,
    end: NaiveDate,
    x: Vec<f64>,
    y: f64,
}
fn arrays(rows: &[&Row]) -> (Vec<Vec<f64>>, Vec<f64>) {
    (
        rows.iter().map(|r| r.x.clone()).collect(),
        rows.iter().map(|r| r.y).collect(),
    )
}
pub fn evaluate(calendar: &Calendar, bars: &[Value], spy: &[Value], symbol: &str) -> Result<Value> {
    let panel = build_panel(calendar, bars, spy)?;
    if panel.len() < 400 {
        return Ok(
            json!({"status":"inconclusive","reason":"At least 400 feature-complete origin dates are required","horizons":[]}),
        );
    }
    let a = (panel.len() as f64 * 0.6) as usize;
    let b = (panel.len() as f64 * 0.8) as usize;
    let calibration_start = panel[a].date;
    let evaluation_start = panel[b].date;
    let train_end = panel[a.saturating_sub(5)].date;
    let calibration_end = panel[b.saturating_sub(5)].date;
    let mut reports = Vec::new();
    for horizon in [1usize, 5] {
        let mut rows = Vec::new();
        for r in &panel {
            let i = r.index;
            if i + horizon >= bars.len() {
                continue;
            }
            let y = financials::number(&bars[i + horizon]["close"])?
                / financials::number(&bars[i + 1]["open"])?
                - 1.;
            if !y.is_finite() {
                return Err(invalid("Nonfinite historical label"));
            }
            rows.push(Row {
                date: r.date,
                end: financials::date(&bars[i + horizon]["session_date"])?,
                x: r.x.clone(),
                y,
            });
        }
        let train: Vec<_> = rows
            .iter()
            .filter(|r| r.date < train_end && r.end < calibration_start)
            .collect();
        let calibration: Vec<_> = rows
            .iter()
            .filter(|r| {
                r.date >= calibration_start && r.date < calibration_end && r.end < evaluation_start
            })
            .collect();
        let test: Vec<_> = rows.iter().filter(|r| r.date >= evaluation_start).collect();
        if [train.len(), calibration.len(), test.len()]
            .into_iter()
            .min()
            .unwrap_or(0)
            < 20
        {
            reports.push(json!({"horizon":horizon,"status":"inconclusive","reason":"Insufficient partition coverage"}));
            continue;
        }
        let (tx, ty) = arrays(&train);
        let (cx, cy) = arrays(&calibration);
        let (vx, truth) = arrays(&test);
        let model = fit_heads(&tx, &ty, Limits::default())?;
        let (predicted, probability) = model.predict(&vx)?;
        let (cal_predicted, _) = model.predict(&cx)?;
        let mut residuals: Vec<_> = cy.iter().zip(cal_predicted).map(|(y, p)| y - p).collect();
        residuals.sort_by(f64::total_cmp);
        let offsets = [
            statistics::quantile(&residuals, 0.1),
            statistics::quantile(&residuals, 0.9),
        ];
        let base_return = statistics::mean(&ty);
        let base_prob = ty.iter().filter(|v| **v > 0.).count() as f64 / ty.len() as f64;
        let baseline_return = vec![base_return; test.len()];
        let baseline_prob = vec![base_prob; test.len()];
        let date_values: Vec<_> = test.iter().map(|r| r.date).collect();
        let years: BTreeSet<_> = date_values.iter().map(Datelike::year).collect();
        let mut by_year = serde_json::Map::new();
        for year in years {
            let selected: Vec<_> = date_values
                .iter()
                .enumerate()
                .filter(|(_, d)| d.year() == year)
                .map(|(i, _)| i)
                .collect();
            let select = |values: &[f64]| selected.iter().map(|i| values[*i]).collect::<Vec<_>>();
            by_year.insert(year.to_string(),json!({"dates":selected.len(),"candidate":scores(&select(&truth),&select(&predicted),&select(&probability))?,"baseline":scores(&select(&truth),&select(&baseline_return),&select(&baseline_prob))?}));
        }
        let mae: Vec<_> = truth
            .iter()
            .zip(&predicted)
            .map(|(y, p)| (y - base_return).abs() - (y - p).abs())
            .collect();
        let brier: Vec<_> = truth
            .iter()
            .zip(&probability)
            .map(|(y, p)| {
                let label = if *y > 0. { 1. } else { 0. };
                (base_prob - label).powi(2) - (p - label).powi(2)
            })
            .collect();
        let mut comparisons = serde_json::Map::new();
        for (metric, delta) in [("mae", mae), ("brier", brier)] {
            let interval =
                statistics::paired_block_interval(&date_values, &delta, 2000, 20, 0.9875)?;
            let state = if date_values.len() < 126 {
                "inconclusive"
            } else if interval
                .as_ref()
                .and_then(|v| v["lower"].as_f64())
                .is_some_and(|v| v > 0.)
            {
                "passed"
            } else {
                "failed"
            };
            comparisons.insert(metric.into(), json!({"status":state,"interval":interval}));
        }
        let state = if comparisons.values().all(|v| v["status"] == "passed") {
            "passed"
        } else if comparisons.values().any(|v| v["status"] == "inconclusive") {
            "inconclusive"
        } else {
            "failed"
        };
        let candidate = scores(&truth, &predicted, &probability)?;
        let coverage = truth
            .iter()
            .zip(&predicted)
            .filter(|(y, p)| **y >= **p + offsets[0] && **y <= **p + offsets[1])
            .count() as f64
            / truth.len() as f64;
        reports.push(json!({"horizon":horizon,"status":state,"symbol":symbol,"training_dates":train.len(),"calibration_dates":calibration.len(),"evaluation_dates":test.len(),"non_overlapping_horizon_count":test.len().div_ceil(horizon),"approximate_20_session_blocks":test.len()/20,"candidate":candidate,"baseline":scores(&truth,&baseline_return,&baseline_prob)?,"comparisons":comparisons,"by_stock":{(symbol):candidate},"by_year":by_year,"interval_coverage":coverage,"interval_method":"Calibration residual 10th–90th percentile; no coverage guarantee under dependence","partitions":{"train_end":train.last().map(|r|r.date),"calibration_start":calibration.first().map(|r|r.date),"calibration_end":calibration.last().map(|r|r.date),"evaluation_start":test.first().map(|r|r.date)},"fitted_parameters":model,"reference_parameters":{"expected_return":base_return,"probability_positive":base_prob},"calibration_interval_offsets":offsets}));
    }
    let status = if !reports.is_empty() && reports.iter().all(|r| r["status"] == "passed") {
        "passed"
    } else if reports.iter().any(|r| r["status"] == "failed") {
        "failed"
    } else {
        "inconclusive"
    };
    Ok(
        json!({"status":status,"symbol":symbol,"protocol":protocol(),"features":FEATURES,"horizons":reports,"limitations":["Single-security exploratory study; watchlist selection is not an unbiased market universe.","Distinct dates are not guaranteed independent; blocks address some serial dependence.","Historical adjusted prices are a reconstructed archive, not prospective observations.","Four-comparison correction applies within this report, not to repeated reports or a search across stocks.","No automatic promotion, trading authority, or claim of profitability."]}),
    )
}
