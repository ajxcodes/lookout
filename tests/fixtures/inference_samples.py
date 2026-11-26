"""Deterministic inference fixtures derived from recorded ``/data`` samples.

Source events were sampled during the Phase 10 discovery pass on the running
container (``ajxcodes-yoga``, ``/data``, ``https://lookout.home.ajx.codes``,
``/api/events``). Every value below is a literal byte/dict/string so CI needs
no live audio, browser, or deployment.

Provenance of each sample is recorded in the accompanying comment; no fixture
contains a third-party model payload or network-generated data.
"""

# Event c6f19512: alarm_type "Sound", ai_tags ["Dog Barking"], Gemini
# known_pet result at confidence 0.95, classifier dog_bark at 0.89. Before the
# fix this was stored as acoustic_anomaly despite the confident pet result.
BARK_SOUND_ALARM_SAMPLE: dict[str, object] = {
    "image": b"portrait snapshot c6f19512 front porch",
    "audio": b"dog barking recording c6f19512",
    "metadata": {
        "alarm_type": "Sound",
        "ai_tags": ["Dog Barking"],
    },
}
BARK_SOUND_ALARM_VLM_RAW = (
    '{"category": "known_pet", "confidence": 0.95, "summary": "Mochi on porch"}'
)
DOG_BARK_LABEL = "dog_bark"
DOG_BARK_CONFIDENCE = 0.89

# Event ea63138a: catalog matched "Mochi" (pet-mochi) but only 4/50 sampled
# known_pet events had matched_entities populated before the fix.
CATALOG_PET_SAMPLE: dict[str, object] = {
    "image": b"snapshot with Mochi dog ea63138a",
    "metadata": {"alarm_type": "Motion"},
}

# Discovery repro for an enrolled resident absent from the catalog-driven category.
CATALOG_RESIDENT_SAMPLE: dict[str, object] = {
    "image": b"snapshot with Alvin",
    "metadata": {"alarm_type": "Motion"},
}

# Event 50956bfc: confident false_positive sample used for non-person suppression.
FALSE_POSITIVE_SAMPLE: dict[str, object] = {
    "image": b"shadow and leaves swaying 50956bfc",
    "metadata": {"ai_tags": ["shadow"]},
}

# Repro strings from the discovery findings.
UNMAPPED_LABEL_OUTPUT = '{"category":"unmapped_new_label","confidence":0.97}'
UNPARSEABLE_TEXT_OUTPUT = (
    "Random unparseable garbage output with no structure or recognizable tokens."
)
RESIDENT_NAME_CUE = b"snapshot with Alvin"
LOW_CONFIDENCE_GLASS_BREAK_AUDIO = b"glass_break:0.05"

# Deterministic byte probes for the sound taxonomy.
SILENCE_AUDIO = b""
GLASS_BREAK_AUDIO = b"emergency glass_break sound sample"
DOG_BARK_AUDIO = b"audio recording with dog_bark"
CAR_ALARM_AUDIO = b"car alarm siren recording"
SMOKE_ALARM_AUDIO = b"smoke alarm chirp"
DOORBELL_AUDIO = b"doorbell ding"
SPEECH_AUDIO = b"speech conversation"
AMBIENT_AUDIO = b"ambient sound bytes"
