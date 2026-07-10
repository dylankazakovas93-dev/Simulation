from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("streamlit")

from app.streamlit_app import apply_score_config, inspect_historical_upload_metadata


def test_zero_payout_low_blow_guidance_is_not_candidate():
    frame = pd.DataFrame(
        [
            {
                "contracts": 1,
                "paths": 250,
                "paid_before_first_blow_count": 0,
                "blew_before_payout_count": 0,
                "paid_before_first_blow_rate": 0.0,
                "blew_before_payout_rate": 0.0,
                "payout_after_rebuy_rate": 0.0,
                "no_resolution_rate": 1.0,
                "any_payout_rate": 0.0,
                "mean_net_cash": 0.0,
            }
        ]
    )
    score_config = {
        "survival_weight": 0.4,
        "ev_weight": 0.3,
        "speed_weight": 0.15,
        "convexity_weight": 0.15,
        "max_blow_rate": 0.5,
    }

    scored = apply_score_config(frame, score_config)

    assert scored.iloc[0]["status"] == "No payout observed"


def test_forward_output_metadata_warns_but_single_rr_single_path_is_not_blocked():
    frame = pd.DataFrame(
        {
            "rr_config_id": ["1rr", "1rr"],
            "path_id": [0, 0],
            "status": ["REALIZED", "SYNTHETIC"],
            "source_trade_packet_id": ["realized-1", "packet-2"],
        }
    )

    errors, warnings = inspect_historical_upload_metadata(frame)

    assert errors == []
    assert len(warnings) == 1
    assert "Historical bootstrap would resample simulated outcomes again" in warnings[0]
