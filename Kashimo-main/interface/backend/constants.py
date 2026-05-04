"""
backend/constants.py
All static data for the GridAgent dashboard simulation.
No Streamlit imports — safe to use from any context.
"""

TICKS_PER_DAY = 144

HOURLY = [
    0.81, 0.80, 0.79, 0.78, 0.77, 0.78, 0.81, 0.88, 0.95,
    1.00, 1.02, 1.03, 1.03, 1.04, 1.05, 1.07, 1.10, 1.13,
    1.15, 1.16, 1.14, 1.09, 1.00, 0.90,
]

LOAD_PROFILE = [
    round(HOURLY[i // 6] + (i % 6) / 6 * (HOURLY[(i // 6 + 1) % 24] - HOURLY[i // 6]), 4)
    for i in range(TICKS_PER_DAY)
]

BASE_LOAD_MW = 1182.0
BASE_GEN_MAX = 2050.0
DC_BASELINE  = 110.0

TX_LINES = [
    "Benning→EastCapitol", "EastCapitol→Greenway", "Greenway→HainsPoint",
    "HainsPoint→Buzzard",  "Buzzard→Georgetown",   "Georgetown→Nevada",
    "Nevada→Benning",      "Benning→Georgetown",   "Nevada→HainsPoint",
    "EastCapitol→Buzzard",
]
BASE_LINE_PCT = [12.1, 9.8, 8.3, 9.1, 11.4, 13.7, 17.8, 21.3, 15.6, 10.9]

# Mirrored from DEMO_SCHEDULE in run_live.py (tick offsets for a 144-tick day)
DEMO_EVENTS = [
    dict(
        tick=36, etype="AI_TRAINING_SPIKE", dur=24,
        p=dict(min_mw=25.0, max_mw=60.0),
        name="Morning Training Job",
        plain=(
            "A large AI model training job just launched at the NoMa data center. "
            "It's pulling significantly more power than normal — like hundreds of extra "
            "computers all turning on at once."
        ),
    ),
    dict(
        tick=84, etype="AI_TRAINING_DROPOUT", dur=18,
        p=dict(dropout=0.75),
        name="Training Job Crash",
        plain=(
            "The AI training job crashed mid-run. Power demand dropped by 75% almost "
            "instantly. Sudden drops like this can be just as destabilizing as sudden spikes."
        ),
    ),
    dict(
        tick=108, etype="COOLING_CASCADE", dur=36,
        p=dict(comp=40.0, cool=18.0, delay=3),
        name="Cooling Cascade",
        plain=(
            "The data center's computers ran hot, so the cooling systems kicked in. "
            "First the compute load spiked, and 30 minutes later the chillers added "
            "even more demand — a double wave."
        ),
    ),
    dict(
        tick=180, etype="LOAD_OSCILLATION", dur=48,
        p=dict(amp=15.0, period=4.0),
        name="Load Oscillation",
        plain=(
            "The data center's power electronics are causing its draw to swing up and "
            "down in a rhythm. This makes it difficult for the grid to stay in balance."
        ),
    ),
]

ACTION_PLAIN = {
    "CURTAIL_LOAD":     ("Reduce data center power now",
                         "Ask the data center to temporarily dial back. Like turning down "
                         "AC during a heat wave to keep the lights on for everyone."),
    "DEFER_WORKLOAD":   ("Delay non-urgent computing tasks",
                         "Ask the data center to pause background jobs and run them tonight "
                         "when the grid has more breathing room."),
    "NO_ACTION":        ("No action needed",
                         "The grid is stable. Everything is within safe limits."),
    "RESTORE_BASELINE": ("Return to normal operations",
                         "The stress has passed. The data center can go back to its normal "
                         "operating level."),
    "ALERT_OPERATOR":   ("Get a human to review",
                         "The situation is complex enough that a person should assess it "
                         "before any automated action is taken."),
}

DC_STATE_PLAIN = {
    "NOMINAL":          "Running normally.",
    "TRAINING SPIKE":   "Running a major AI training job — power demand is well above normal.",
    "TRAINING DROPOUT": "A training job just crashed — demand dropped sharply and unexpectedly.",
    "COOLING CASCADE":  "Compute and cooling systems are both surging — a double demand wave.",
    "OSCILLATING":      "Power draw is swinging up and down rhythmically — hard for the grid to track.",
}
