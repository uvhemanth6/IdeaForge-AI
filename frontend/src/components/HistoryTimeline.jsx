import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { Clock, GitCompare, ExternalLink, Trash2 } from "lucide-react";
import LikeButton from "./LikeButton";

function formatDateTime(value) {
  if (!value) return "Unknown date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export default function HistoryTimeline({ entries, selectedIds, onToggle, onDelete }) {
  const orderedEntries = [...entries].sort(
    (a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()
  );

  return (
    <div className="grid gap-3">
      {orderedEntries.map((entry, i) => {
        const active = selectedIds.includes(entry.id);
        return (
          <motion.div
            key={entry.id}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.03 }}
            className={`text-left rounded-xl border p-4 transition-all card-hover ${
              active
                ? "border-terracotta/20 dark:border-aurora-purple/20 bg-ink/90 dark:bg-night-card text-cream"
                : "border-ink/4 dark:border-white/4 bg-white/50 dark:bg-white/3 text-ink hover:-translate-y-0.5"
            }`}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.14em] ${
                      active ? "text-cream/60" : "text-ink/45 dark:text-white/40"
                    }`}
                  >
                    <Clock className="h-3 w-3" />
                    {formatDateTime(entry.created_at)}
                  </span>
                  {i === 0 && (
                    <span className="inline-flex rounded-full bg-moss/15 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-moss dark:bg-aurora-green/15 dark:text-aurora-green">
                      Latest
                    </span>
                  )}
                </div>
                <h3 className="mt-1.5 font-display text-xl">{entry.idea.idea_name}</h3>
              </div>
              <div className="flex items-center gap-2.5">
                <LikeButton ideaId={entry.idea.idea_name} entry={entry} size="sm" />
                <div className="text-right">
                  <p className={`text-xs ${active ? "text-cream/50" : "text-ink/35 dark:text-white/30"}`}>Score</p>
                  <p className="font-display text-3xl">{entry.report.score}</p>
                </div>
              </div>
            </div>
            <p className={`mt-3 text-sm leading-5 line-clamp-2 ${active ? "text-cream/50" : "text-ink/45 dark:text-white/35"}`}>
              {entry.idea.problem_statement}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onToggle(entry.id)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] transition-all ${
                  active
                    ? "border-white/15 bg-white/10 text-cream"
                    : "border-ink/8 bg-white/40 text-ink/55 dark:border-white/8 dark:bg-white/5 dark:text-white/45"
                }`}
              >
                <GitCompare className="h-3.5 w-3.5" />
                {active ? "Selected" : "Compare"}
              </button>
              <Link
                to={`/results/${entry.id}`}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] transition-all ${
                  active
                    ? "bg-white/10 text-cream"
                    : "bg-ink text-cream dark:bg-white/10 dark:text-white"
                }`}
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Open report
              </Link>
              <button
                type="button"
                onClick={() => onDelete(entry.id)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] transition-all ${
                  active
                    ? "border-terracotta/35 bg-terracotta/15 text-dusty-rose"
                    : "border-terracotta/20 bg-terracotta/5 text-terracotta dark:border-aurora-pink/20 dark:bg-aurora-pink/8 dark:text-aurora-pink"
                }`}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete
              </button>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
