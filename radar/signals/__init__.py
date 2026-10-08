"""All per-stock signals. Each returns a SignalResult with a score in [-1, 1], a confidence in [0, 1],
Hebrew reasons, and optional risk points."""

SIGNAL_LABELS = {
    "momentum": "מומנטום מחיר",
    "volume_accumulation": "צבירת מחזורים",
    "breakout_setup": "מבנה פריצה",
    "insider_buying": "קניות מנהלים",
    "analyst_revisions": "עדכוני תחזיות אנליסטים",
    "analyst_ratings": "שדרוגי אנליסטים",
    "price_target": "מחיר יעד",
    "short_squeeze": "פוטנציאל שורט סקוויז",
    "options_flow": "פעילות אופציות",
    "news": "חדשות",
    "catalysts": "אירועים צפויים",
    "gov_contracts": "חוזים ממשלתיים",
    "geopolitics": "מלחמות ומתיחות",
    "macro": "מאקרו",
    "country": "נתוני מדינה",
}
