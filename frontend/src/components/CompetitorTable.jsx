import { ExternalLink } from "lucide-react";
import { motion } from "framer-motion";

export default function CompetitorTable({ competitors }) {
  return (
    <motion.section initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="glass-panel rounded-2xl p-5">
      <div className="mb-4">
        <span className="pill">Competitor Insights</span>
        <h3 className="mt-2 font-display text-2xl text-ink">Live landscape scan</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-y-2">
          <thead>
            <tr className="text-left text-[10px] font-semibold uppercase tracking-[0.16em] text-ink/30 dark:text-white/25">
              <th className="px-3 py-1">Competitor</th>
              <th className="px-3 py-1">Summary</th>
              <th className="px-3 py-1">Strength</th>
              <th className="px-3 py-1">Weakness</th>
              <th className="px-3 py-1">Source</th>
            </tr>
          </thead>
          <tbody>
            {competitors.map((c, i) => {
              const isUrl = typeof c.source === "string" && /^https?:\/\//i.test(c.source);
              return (
                <motion.tr
                  key={`${c.name}-${i}`}
                  className="rounded-xl bg-white/50 dark:bg-white/3 text-sm text-ink/60 dark:text-white/50"
                  initial={{ opacity: 0, x: -16 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.08 + i * 0.06 }}
                >
                  <td className="rounded-l-xl px-3 py-3 font-semibold text-ink">
                    <div>{c.name}</div>
                    {Array.isArray(c.signals) && c.signals.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {c.signals.slice(0, 3).map((sig) => (
                          <span
                            key={sig}
                            className="inline-flex rounded-full bg-ink/5 dark:bg-white/10 px-2 py-0.5 text-[10px] font-medium text-ink/55 dark:text-white/60"
                          >
                            {sig}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3">{c.summary}</td>
                  <td className="px-3 py-3">{c.strength}</td>
                  <td className="px-3 py-3">{c.weakness}</td>
                  <td className="rounded-r-xl px-3 py-3 whitespace-nowrap">
                    {isUrl ? (
                      <a
                        href={c.source}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 rounded-lg border border-ink/10 dark:border-white/10 px-2.5 py-1 text-xs font-semibold text-terracotta dark:text-aurora-pink hover:underline"
                      >
                        {c.domain || "View"}
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    ) : (
                      <span className="text-xs uppercase tracking-wider text-ink/40 dark:text-white/35">
                        {c.source || "unknown"}
                      </span>
                    )}
                  </td>
                </motion.tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </motion.section>
  );
}
