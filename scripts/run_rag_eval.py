from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from internal_assistant.rag import RetrievalConfig

from evaluation.runners import run_full_eval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ejecuta la evaluacion RAG completa.")
    parser.add_argument(
        "--dataset",
        default=os.getenv("EVAL_DATASET_PATH", "evaluation/datasets/rag_eval_questions.json"),
    )
    parser.add_argument(
        "--adversarial-dataset",
        default="evaluation/datasets/adversarial_questions.json",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("EVAL_OUTPUT_DIR", "evaluation/reports"),
    )
    parser.add_argument(
        "--provider",
        choices=("mock", "openai", "openai_compatible", "anthropic_foundry", "azure_foundry"),
        default=os.getenv("EVAL_PROVIDER", "mock"),
    )
    parser.add_argument("--include-adversarial", action="store_true")
    parser.add_argument("--include-ablation", action="store_true")
    parser.add_argument(
        "--use-llm-judge",
        action="store_true",
        default=os.getenv("EVAL_USE_LLM_JUDGE", "false").strip().lower() == "true",
    )
    parser.add_argument("--top-k", type=int, default=int(os.getenv("EVAL_TOP_K", "5")))
    parser.add_argument("--vector-weight", type=float, default=float(os.getenv("EVAL_VECTOR_WEIGHT", "0.70")))
    parser.add_argument("--text-weight", type=float, default=float(os.getenv("EVAL_TEXT_WEIGHT", "0.30")))
    parser.add_argument("--max-concurrency", type=int, default=int(os.getenv("EVAL_MAX_CONCURRENCY", "1")))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_full_eval(
        dataset_path=args.dataset,
        adversarial_dataset_path=args.adversarial_dataset,
        output_dir=args.output_dir,
        provider_name=args.provider,
        retrieval_config=RetrievalConfig(
            top_k=args.top_k,
            vector_weight=args.vector_weight,
            text_weight=args.text_weight,
        ),
        include_adversarial=args.include_adversarial,
        include_ablation=args.include_ablation,
        use_llm_judge=args.use_llm_judge,
        max_concurrency=args.max_concurrency,
    )
    paths = report.get("report_paths", {})
    print(f"Reporte JSON: {paths.get('json', '')}")
    print(f"Reporte Markdown: {paths.get('markdown', '')}")
    print(f"Questions evaluated: {report['summary']['Questions evaluated']}")
    print(f"Retrieval hit@5: {report['summary']['Retrieval hit@5']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
