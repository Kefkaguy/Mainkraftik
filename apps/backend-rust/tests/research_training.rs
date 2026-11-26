use mainkraft::research_training::{Heads, Limits, Trainer, fit_heads};
use serde_json::Value;
fn values(value: &Value) -> Vec<f64> {
    serde_json::from_value(value.clone()).unwrap()
}
fn close(label: &str, actual: &[f64], expected: &Value, tolerance: f64) {
    let expected = values(expected);
    assert_eq!(actual.len(), expected.len());
    for (i, (a, b)) in actual.iter().zip(expected).enumerate() {
        assert!(
            (a - b).abs() <= tolerance * (1. + b.abs()),
            "{label}[{i}]: {a} != {b}"
        );
    }
}
#[test]
fn original_research_objective_scaling_parameters_and_predictions_match() {
    let fixture: Value = serde_json::from_str(if cfg!(target_os = "linux") {
        include_str!("fixtures/research-training-linux.json")
    } else {
        include_str!("fixtures/research-training.json")
    })
    .unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let x: Vec<Vec<f64>> = serde_json::from_value(case["x"].clone()).unwrap();
        let y = values(&case["y"]);
        let model = fit_heads(&x, &y, Limits::default()).unwrap();
        assert_eq!(
            model.mean,
            values(&case["parameters"]["mean"]),
            "{} normalization means",
            case["name"]
        );
        close("scale", &model.scale, &case["parameters"]["scale"], 1e-12);
        close("ridge", &model.ridge, &case["parameters"]["ridge"], 2e-9);
        close(
            "logistic",
            &model.logistic,
            &case["parameters"]["logistic"],
            2e-9,
        );
        let evaluation: Vec<Vec<f64>> = serde_json::from_value(case["evaluation"].clone()).unwrap();
        let (r, p) = model.predict(&evaluation).unwrap();
        close("return", &r, &case["returns"], 1e-9);
        close("probability", &p, &case["probability"], 1e-9);
        let (r, p) = model.predict(&x).unwrap();
        close("train return", &r, &case["train_returns"], 1e-9);
        close("train probability", &p, &case["train_probability"], 1e-9);
        let restored: Heads =
            serde_json::from_value(serde_json::to_value(&model).unwrap()).unwrap();
        assert_eq!(
            restored.predict(&evaluation).unwrap(),
            model.predict(&evaluation).unwrap()
        );
    }
    for case in fixture["errors"].as_array().unwrap() {
        let x = serde_json::from_value::<Vec<Vec<f64>>>(case["x"].clone()).unwrap();
        match fit_heads(&x, &values(&case["y"]), Limits::default()).unwrap_err() {
            mainkraft::Error::Invalid(s) => assert_eq!(s, case["error"]),
            _ => panic!("error class"),
        }
    }
}
#[tokio::test]
async fn invalid_training_resources_and_recovery_do_not_emit_a_model() {
    let x = vec![vec![1., 2.]; 20];
    let mut y = vec![1.; 20];
    y[0] = -1.;
    for limits in [
        Limits {
            rows: 19,
            ..Limits::default()
        },
        Limits {
            columns: 1,
            ..Limits::default()
        },
        Limits {
            cells: 3,
            ..Limits::default()
        },
        Limits {
            projected_work: 1,
            ..Limits::default()
        },
    ] {
        assert!(fit_heads(&x, &y, limits).is_err());
    }
    let mut bad = x.clone();
    bad[0][0] = f64::NAN;
    assert!(fit_heads(&bad, &y, Limits::default()).is_err());
    bad = x.clone();
    bad[0].pop();
    assert!(fit_heads(&bad, &y, Limits::default()).is_err());
    assert!(fit_heads(&x, &y[..19], Limits::default()).is_err());
    let worker = Trainer::new(Limits::default());
    assert!(worker.fit(bad, y.clone()).await.is_err());
    let model = worker.fit(x, y).await.unwrap();
    assert!(model.predict(&[vec![1.]]).is_err());
}
