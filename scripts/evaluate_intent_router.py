from collections import Counter
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple.evaluation.dataset import load_benchmark
from ripple.retrieval.intent import QueryIntent, classify_intent


BENCHMARK_PATH = Path("data/benchmark.json")
GOLD_INTENT_BY_CATEGORY = {
    "lookup": QueryIntent.LOOKUP,
    "attribute": QueryIntent.ATTRIBUTE,
    "relational": QueryIntent.DEPENDENCY,
    "blast_radius": QueryIntent.BLAST_RADIUS,
}
GOLD_LABELS = (
    QueryIntent.LOOKUP,
    QueryIntent.ATTRIBUTE,
    QueryIntent.DEPENDENCY,
    QueryIntent.BLAST_RADIUS,
)
PREDICTED_LABELS = (*GOLD_LABELS, QueryIntent.AMBIGUOUS_RELATIONSHIP)


def main() -> None:
    entries = load_benchmark(BENCHMARK_PATH)
    matrix: Counter[tuple[QueryIntent, QueryIntent]] = Counter()
    mismatches: list[tuple[str, str, QueryIntent, QueryIntent]] = []

    for entry in entries:
        expected = GOLD_INTENT_BY_CATEGORY[entry.category]
        predicted = classify_intent(entry.question)
        matrix[(expected, predicted)] += 1
        if predicted is not expected:
            mismatches.append(
                (entry.id, entry.question, expected, predicted)
            )

    correct = len(entries) - len(mismatches)
    print(
        "Router accuracy: "
        f"{correct}/{len(entries)} ({correct / len(entries):.1%})"
    )
    print("\nConfusion matrix (rows=gold, columns=predicted)")
    print("gold\\pred " + " ".join(label.value for label in PREDICTED_LABELS))
    for expected in GOLD_LABELS:
        counts = " ".join(
            str(matrix[(expected, predicted)])
            for predicted in PREDICTED_LABELS
        )
        print(f"{expected.value} {counts}")

    print("\nPer-intent metrics")
    print("intent precision recall")
    for label in GOLD_LABELS:
        true_positive = matrix[(label, label)]
        predicted_count = sum(
            matrix[(expected, label)]
            for expected in GOLD_LABELS
        )
        gold_count = sum(
            matrix[(label, predicted)]
            for predicted in PREDICTED_LABELS
        )
        precision = (
            true_positive / predicted_count if predicted_count else 0.0
        )
        recall = true_positive / gold_count if gold_count else 0.0
        print(f"{label.value} {precision:.3f} {recall:.3f}")

    ambiguous_count = sum(
        predicted is QueryIntent.AMBIGUOUS_RELATIONSHIP
        for _id, _question, _expected, predicted in mismatches
    )
    print(f"\nAmbiguous fallbacks: {ambiguous_count}")
    print(f"Misclassifications: {len(mismatches)}")
    for entry_id, question, expected, predicted in mismatches:
        print(
            f"- {entry_id}: expected={expected.value} "
            f"predicted={predicted.value}: {question}"
        )


if __name__ == "__main__":
    main()
