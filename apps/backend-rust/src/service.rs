//! Owned service supervision. This does not grant brokerage execution authority.
//! A component exit stops its peers; accepted work drains before shutdown returns.
use crate::{Result, discovery::Scheduler, engine::Engine, invalid};
use futures::future::BoxFuture;
use std::{collections::BTreeSet, future::Future, sync::Arc, time::Duration};
use tokio::{net::TcpListener, sync::watch, task::JoinSet};

type Factory = Box<dyn FnOnce(watch::Receiver<bool>) -> BoxFuture<'static, Result<()>> + Send>;
pub struct Component {
    name: &'static str,
    start: Factory,
}
impl Component {
    pub fn new<F, Fut>(name: &'static str, start: F) -> Self
    where
        F: FnOnce(watch::Receiver<bool>) -> Fut + Send + 'static,
        Fut: Future<Output = Result<()>> + Send + 'static,
    {
        Self {
            name,
            start: Box::new(move |stop| Box::pin(start(stop))),
        }
    }
}
fn stopping(receiver: &watch::Receiver<bool>) -> bool {
    *receiver.borrow() || receiver.has_changed().is_err()
}
async fn stopped(mut receiver: watch::Receiver<bool>) {
    while !stopping(&receiver) {
        if receiver.changed().await.is_err() {
            break;
        }
    }
}

/// A forced deadline is an error, never a clean cutover or successful handoff.
/// Dropping the supervisor aborts its owned JoinSet; durable pending intents must
/// then be reconciled by the next worker, as for an operating-system process kill.
pub async fn supervise(
    components: Vec<Component>,
    external_stop: watch::Receiver<bool>,
    grace: Duration,
) -> Result<()> {
    let names: BTreeSet<_> = components.iter().map(|c| c.name).collect();
    if components.is_empty()
        || names.len() != components.len()
        || names.contains("")
        || grace.is_zero()
        || grace > Duration::from_secs(300)
    {
        return Err(invalid("invalid service supervision configuration"));
    }
    if stopping(&external_stop) {
        return Ok(());
    }
    let (sender, receiver) = watch::channel(false);
    let mut tasks = JoinSet::new();
    for component in components {
        let stop = receiver.clone();
        tasks.spawn(async move { (component.name, (component.start)(stop).await) });
    }
    let mut failure = None;
    tokio::select! {
        _=stopped(external_stop)=>{},
        result=tasks.join_next()=>{
            failure=Some(match result {
                Some(Ok((name,_)))=>format!("service component exited: {name}"),
                _=>"service component panicked".into(),
            });
        }
    }
    sender.send_replace(true);
    let deadline = tokio::time::sleep(grace);
    tokio::pin!(deadline);
    while !tasks.is_empty() {
        tokio::select! {
            result=tasks.join_next()=>{
                match result {
                    Some(Ok((_,Ok(()))))=>{},
                    Some(Ok((name,Err(_))))=>{failure.get_or_insert_with(||format!("service component failed while draining: {name}"));},
                    _=>{failure.get_or_insert_with(||"service component panicked while draining".into());},
                }
            },
            _=&mut deadline=>{
                tasks.abort_all();
                while tasks.join_next().await.is_some() {}
                return Err(invalid("service drain deadline exceeded; recovery reconciliation required"));
            }
        }
    }
    if let Some(reason) = failure {
        Err(invalid(reason))
    } else {
        Ok(())
    }
}

/// Existing experiment roles only: HTTP bridge, two-account engine and discovery.
/// Dependencies are already validated components. No environment defaults,
/// credential mutation, migration, account creation or execution permit is added.
pub async fn paper(
    engine: Engine,
    scheduler: Arc<Scheduler>,
    router: axum::Router,
    listener: TcpListener,
    stop: watch::Receiver<bool>,
) -> Result<()> {
    supervise(
        vec![
            Component::new("paper-engine", move |stop| async move {
                engine.run(stop).await;
                Ok(())
            }),
            Component::new("paper-discovery", move |stop| async move {
                scheduler.run(stop).await
            }),
            Component::new("paper-http", move |stop| async move {
                axum::serve(listener, router)
                    .with_graceful_shutdown(stopped(stop))
                    .await?;
                Ok(())
            }),
        ],
        stop,
        Duration::from_secs(120),
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    use tokio::sync::Notify;

    #[tokio::test]
    async fn component_failure_stops_peers_without_discarding_owned_work() {
        let accepted = Arc::new(Notify::new());
        let release = Arc::new(Notify::new());
        let acknowledged = Arc::new(AtomicBool::new(false));
        let (sender, receiver) = watch::channel(false);
        let started = accepted.clone();
        let finish = release.clone();
        let saved = acknowledged.clone();
        let failed = accepted.clone();
        let mut running = tokio::spawn(supervise(
            vec![
                Component::new("ack", move |mut stop| async move {
                    started.notify_one();
                    stop.changed().await.unwrap();
                    assert!(*stop.borrow());
                    finish.notified().await;
                    saved.store(true, Ordering::SeqCst);
                    Ok(())
                }),
                Component::new("failed", move |_| async move {
                    failed.notified().await;
                    Err(invalid("fixture failure"))
                }),
            ],
            receiver,
            Duration::from_secs(5),
        ));
        assert!(
            tokio::time::timeout(Duration::from_millis(100), &mut running)
                .await
                .is_err()
        );
        assert!(!acknowledged.load(Ordering::SeqCst));
        release.notify_one();
        let error = running.await.unwrap().unwrap_err().to_string();
        assert!(error.contains("failed"));
        assert!(acknowledged.load(Ordering::SeqCst));
        drop(sender);
    }
    #[tokio::test]
    async fn closed_or_stopped_supervisor_does_not_start_components() {
        for closed in [false, true] {
            let (sender, receiver) = watch::channel(!closed);
            if closed {
                drop(sender);
            }
            supervise(
                vec![Component::new("never", |_| async {
                    panic!("started after stop")
                })],
                receiver,
                Duration::from_secs(1),
            )
            .await
            .unwrap();
        }
        let (_, receiver) = watch::channel(false);
        assert!(
            supervise(vec![], receiver, Duration::from_secs(1))
                .await
                .is_err()
        );
    }
    struct Dropped(Arc<AtomicBool>);
    impl Drop for Dropped {
        fn drop(&mut self) {
            self.0.store(true, Ordering::SeqCst);
        }
    }
    #[tokio::test]
    async fn forced_deadline_is_failure_and_releases_owned_tasks() {
        let dropped = Arc::new(AtomicBool::new(false));
        let flag = dropped.clone();
        let (sender, receiver) = watch::channel(false);
        let (started, ready) = tokio::sync::oneshot::channel();
        let running = tokio::spawn(supervise(
            vec![Component::new("stuck", move |_| async move {
                let _owned = Dropped(flag);
                started.send(()).unwrap();
                std::future::pending::<()>().await;
                Ok(())
            })],
            receiver,
            Duration::from_millis(50),
        ));
        ready.await.unwrap();
        sender.send(true).unwrap();
        let error = running.await.unwrap().unwrap_err().to_string();
        assert!(error.contains("recovery reconciliation required"));
        assert!(dropped.load(Ordering::SeqCst));
    }
}
