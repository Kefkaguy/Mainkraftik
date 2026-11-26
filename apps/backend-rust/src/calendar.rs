//! Versioned exchange sessions exported from the exact Python calendar reference.
//! Never silently replace unavailable sessions with a weekday approximation.
use crate::{Result, invalid};
use chrono::{DateTime, Datelike, Duration, NaiveDate, TimeZone, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Session {
    pub date: NaiveDate,
    pub open: DateTime<Utc>,
    pub close: DateTime<Utc>,
}
#[derive(Clone, Debug)]
pub struct Calendar {
    sessions: BTreeMap<NaiveDate, Session>,
}
impl Calendar {
    pub fn from_json(bytes: &[u8]) -> Result<Self> {
        let rows: Vec<Session> = serde_json::from_slice(bytes)?;
        let mut sessions = BTreeMap::new();
        for row in rows {
            if row.close <= row.open || row.date != row.open.with_timezone(&New_York).date_naive() {
                return Err(invalid("invalid exchange session"));
            }
            let date = row.date;
            if sessions.insert(date, row).is_some() {
                return Err(invalid("duplicate exchange session"));
            }
        }
        Ok(Self { sessions })
    }
    pub fn embedded() -> Result<Self> {
        Self::from_json(include_bytes!("../assets/exchange-sessions.json"))
    }
    pub fn session(&self, date: NaiveDate) -> Option<&Session> {
        self.sessions.get(&date)
    }
    pub fn sessions_between(&self, start: NaiveDate, end: NaiveDate) -> Result<Vec<NaiveDate>> {
        if start > end || start.year() < 2000 || end.year() > 2040 {
            return Err(invalid("exchange calendar range unavailable"));
        }
        Ok(self
            .sessions
            .range(start..=end)
            .map(|(date, _)| *date)
            .collect())
    }
    pub fn issuance_window(
        &self,
        instant: DateTime<Utc>,
    ) -> Result<(Option<NaiveDate>, Option<&'static str>)> {
        let today = instant.with_timezone(&New_York).date_naive();
        let mut eligible = Vec::new();
        for day in self.sessions_between(today - Duration::days(10), today)? {
            if local_time(day, 20, 30)? <= instant {
                eligible.push(day);
            }
        }
        let Some(origin) = eligible.into_iter().next_back() else {
            return Ok((None, Some("Waiting for 20:30 America/New_York issuance")));
        };
        let entry = self
            .sessions_between(origin + Duration::days(1), origin + Duration::days(35))?
            .into_iter()
            .next()
            .ok_or_else(|| invalid("entry session unavailable"))?;
        if instant >= self.sessions[&entry].open {
            return Ok((
                None,
                Some("Previous entry window has passed; waiting for the next scheduled issuance"),
            ));
        }
        Ok((Some(origin), None))
    }
    pub fn prior_sessions(&self, date: NaiveDate, days: i64, count: usize) -> Vec<NaiveDate> {
        let start = date - Duration::days(days);
        let mut rows: Vec<_> = self
            .sessions
            .range(start..date)
            .map(|(date, _)| *date)
            .collect();
        if rows.len() > count {
            rows.drain(..rows.len() - count);
        }
        rows
    }
    pub fn target_time(&self, instant: DateTime<Utc>) -> Result<DateTime<Utc>> {
        let start = instant.with_timezone(&New_York).date_naive();
        let end = instant.date_naive() + Duration::days(14);
        let mut remaining = Duration::hours(1);
        for (_, session) in self.sessions.range(start..=end) {
            let begin = instant.max(session.open);
            let available = session.close - begin;
            if available <= Duration::zero() {
                continue;
            }
            if available >= remaining {
                return Ok(begin + remaining);
            }
            remaining -= available;
        }
        Err(invalid("exchange calendar unavailable for target"))
    }
}
pub fn local_time(date: NaiveDate, hour: u32, minute: u32) -> Result<DateTime<Utc>> {
    New_York
        .from_local_datetime(
            &date
                .and_hms_opt(hour, minute, 0)
                .ok_or_else(|| invalid("invalid local time"))?,
        )
        .single()
        .map(|v| v.with_timezone(&Utc))
        .ok_or_else(|| invalid("ambiguous local time"))
}
pub fn cutoff_for(date: NaiveDate) -> Result<DateTime<Utc>> {
    local_time(date, 20, 0)
}
