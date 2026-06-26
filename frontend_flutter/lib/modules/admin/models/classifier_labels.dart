/// Content categories and quality levels for classifier admin feedback.
const classifierCategories = <String>[
  'service',
  'news',
  'tool',
  'payment',
  'nft',
  'governance',
  'generic',
];

const classifierQualityLevels = <String>[
  'high',
  'medium',
  'low',
  'spam',
];

String classifierQualityLabel(String value) {
  switch (value) {
    case 'high':
      return 'High';
    case 'medium':
      return 'Medium';
    case 'low':
      return 'Low';
    case 'spam':
      return 'Spam';
    default:
      return value;
  }
}

String classifierCategoryLabel(String value) {
  if (value.isEmpty) return 'Generic';
  return value[0].toUpperCase() + value.substring(1);
}

/// Gatekeeper error-type taxonomy — must match the backend annotator's TAXONOMY
/// (FACTUALITY_TYPES | TONE_TYPES). Used when tagging validation anchors.
const gatekeeperErrorTypes = <String>[
  'numeric_drift',
  'unsupported_elaboration',
  'entity_swap',
  'cross_contamination',
  'relational_hallucination',
  'temporal_collapse',
  'hype',
  'speculative_tone',
  'clickbait',
];

String gatekeeperErrorTypeLabel(String value) =>
    value.split('_').map((w) => w.isEmpty ? w : w[0].toUpperCase() + w.substring(1)).join(' ');
