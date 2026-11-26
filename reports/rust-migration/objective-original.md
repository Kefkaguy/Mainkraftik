**1. Бот прогнозирует не совсем то, чем торгует.**

Текущая модель обучена на дневных торговых результатах. В двухаккаунтном эксперименте её прогноз делится на 6.5, чтобы получить оценку движения за час.

Это приближение, а не обученная часовая модель.

**2. Точность прогноза не равна прибыльности торговли.**

Даже правильное предсказание направления может оказаться убыточным после спреда, проскальзывания и частого изменения позиций.

Твой бот пересматривает распределение капитала каждые пять минут. Мы пока не установили, приносит ли эта частота пользу или создаёт дополнительные расходы.



Fix those 2 issues

Also

# KefTrade / Mainkraft — Full Rust Migration

Migrate the existing KefTrade / Mainkraft system from Python to Rust.

This is a production architecture migration, not a new trading strategy. Preserve the current two-account Alpaca PAPER experiment and its behavior.

## 1. Migrate the backend

Rewrite the Python backend in Rust, including:

- Paper Trading execution engine
- Broker API integration
- Market data ingestion
- Model inference and prediction pipelines
- Online residual learning
- Portfolio allocation and rebalancing
- Position and order management
- Risk controls and account isolation
- News processing and discovery
- Research and backtesting infrastructure
- PostgreSQL database operations
- Background workers and scheduling
- Authentication and API services

Use Tokio for asynchronous execution, Axum for the API, SQLx for PostgreSQL, and Serde for serialization where appropriate.

Preserve the current website's functionality and API contracts. Do not unnecessarily rewrite the working frontend unless required for a genuinely complete Rust-based stack.

## 2. Preserve the ML models

Support the existing:

- Ridge Regression
- Logistic Regression
- HistGradientBoosting
- Online Residual Ridge
- Existing frozen model bundles

Implement equivalent inference in Rust using compatible model artifacts.

Verify numerical parity between Python and Rust predictions using deterministic test fixtures.

Do not silently substitute different algorithms or retrain frozen models.

Migrate model training and research workflows where technically feasible. Identify functionality without a faithful Rust equivalent rather than inventing replacements.

## 3. Improve system reliability

- Eliminate unnecessary Python worker overhead.
- Reduce redundant API and database requests.
- Use bounded concurrency and efficient memory management.
- Preserve append-only learning and execution records.
- Prevent duplicate order submissions after restarts.
- Preserve reconciliation of unknown and partially filled orders.
- Ensure failures cannot corrupt position state.
- Implement structured logging and performance metrics.

Benchmark Rust against the existing Python implementation, measuring actual latency, memory usage and throughput.

## 4. Preserve the PAPER experiment

Account 1 must retain its existing fixed numerical model.

Account 2 must retain its existing numerical prior plus Online Residual Ridge learning.

Preserve their current experimental portfolio policies, account identities, positions and learning histories.

No Shadow Mode and no additional trading accounts.

Only Alpaca PAPER endpoints are permitted. Never implement or access live-money trading endpoints.

## 5. Investigate the actual trading weaknesses

After establishing functional parity, separately investigate:

- Daily-to-hourly forecast approximation
- Prediction horizon versus rebalance frequency
- Trading costs and portfolio turnover
- Forecast error versus realized trading P/L
- Whether online learning improves predictions

Report evidence-backed improvements separately. Do not silently change trading behavior during the language migration.

## 6. Safe migration and deployment

Work in stages:

1. Audit all Python modules and their dependencies.
2. Build a complete migration map.
3. Implement Rust services.
4. Run unit and integration tests.
5. Compare Rust outputs against Python outputs.
6. Test with isolated PostgreSQL databases and fake broker responses.
7. Verify restore and rollback procedures.
8. Deploy only after all critical parity checks pass.

Preserve the existing databases, model artifacts, research results and Phase 7/8 evidence.

Do not touch Minecraft, unrelated VPS services or the older KefTrade application.

Never run both trading engines against the same PAPER accounts simultaneously.

Do not deploy an incomplete implementation or remove the existing working system before successful verification.

## 7. Final report

Show:

- CPU and RAM comparison
- Any unresolved risks

Do not claim the Rust implementation is faster without benchmark evidence.

The objective is a maintainable, reliable, high-performance Rust trading system that preserves existing PAPER behavior and supports further ML development.

Do all this