"use client";

import { useActionState, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  clampMsElapsed,
  flattenModules,
  isValidResponse,
  moduleIndexForItem,
  progressForModules,
  widgetForItem,
  type TakerItem,
  type TakerModule,
} from "@/lib/taker";

import { submitAssessment, type SubmitState } from "./actions";

/**
 * The assessment itself (plan.md §13, M6).
 *
 * "Polished but conventional" — the §13 decision, taken over full game
 * mechanics because a 12-to-15-week solo build has better places to spend the
 * time. Concretely: one item per screen, a progress *path* rather than a
 * 47/110 counter, a warm screen between modules, and no points, badges or
 * unlockables.
 *
 * The part that matters most is invisible: **an answer is posted the moment it
 * is tapped, and the UI does not wait for the response.** M6's done-when is
 * "kill the connection mid-module, reopen, resume with nothing lost", and
 * awaiting each write would make all 110 taps feel like a form on school wifi.
 * Failed writes go to an in-memory retry queue, get another attempt with
 * backoff, and are flushed on `pagehide`. Anything that still never lands is
 * simply unanswered, so `nextUnansweredIndex` re-asks it on resume — which is
 * why the queue can afford to give up rather than block.
 *
 * Nothing is written to localStorage. School phones get shared (§16), and the
 * database already holds every answer that matters.
 */

const MAX_ATTEMPTS = 4;

type Pending = {
  itemId: string;
  value: number;
  msElapsed: number;
  attempts: number;
};

const MODULE_TITLE: Record<string, string> = {
  interests: "Things you might do",
  personality: "How you go about things",
  get2: "How you approach opportunities",
};

/** The lead-in above each item. Falls back rather than assuming an instrument. */
const MODULE_PROMPT: Record<string, string> = {
  interests: "Would you enjoy doing this?",
  personality: "How much does this sound like you?",
};

/** Labels for an N-point scale. Keyed by width so a new instrument gets sensible
 *  wording without a code change (R10) — GET2's 0..2 lands on the 3-point row. */
const SCALE_LABELS: Record<number, string[]> = {
  3: ["Disagree", "Not sure", "Agree"],
  5: ["Very inaccurate", "Inaccurate", "Neither", "Accurate", "Very accurate"],
};

function scaleLabels(item: TakerItem): string[] {
  const width = item.response_max - item.response_min + 1;
  return (
    SCALE_LABELS[width] ??
    Array.from({ length: width }, (_, i) => String(item.response_min + i))
  );
}

export function Taker({
  token,
  modules,
  answeredItemIds,
  startIndex,
}: {
  token: string;
  modules: TakerModule[];
  answeredItemIds: string[];
  startIndex: number;
}) {
  const items = useMemo(() => flattenModules(modules), [modules]);

  const [index, setIndex] = useState(startIndex);
  const [answered, setAnswered] = useState<Set<string>>(() => new Set(answeredItemIds));
  // Shown when the student crosses into a module they have not started, and on
  // arrival if they are resuming exactly at a boundary.
  const [showTransition, setShowTransition] = useState(
    () => startIndex > 0 && startIndex < items.length && isModuleStart(modules, startIndex),
  );

  const queue = useRef<Pending[]>([]);
  const draining = useRef(false);
  // Zero until the effect below runs on mount. Not `Date.now()` here: a ref
  // initialiser runs during render, and render must be pure — React may run it
  // more than once and the timings would drift.
  const shownAt = useRef<number>(0);
  const [unsent, setUnsent] = useState(0);

  const item: TakerItem | undefined = items[index];

  // Restart the clock whenever a new item is on screen. Sent as ms_elapsed and
  // read by flags.py's too_fast check, so it has to measure this item rather
  // than the time since the page loaded.
  useEffect(() => {
    shownAt.current = Date.now();
  }, [index, showTransition]);

  const drain = useCallback(async () => {
    if (draining.current) return;
    draining.current = true;

    while (queue.current.length > 0) {
      const next = queue.current[0];
      try {
        const response = await fetch(`/a/${token}/answer`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            itemId: next.itemId,
            value: next.value,
            msElapsed: next.msElapsed,
          }),
          keepalive: true,
        });
        if (!response.ok) throw new Error(String(response.status));
        queue.current.shift();
      } catch {
        next.attempts += 1;
        if (next.attempts >= MAX_ATTEMPTS) {
          // Give up on this one. It stays unanswered in the database, so resume
          // re-asks it — a re-asked question is a far better outcome than a
          // spinner that never resolves.
          queue.current.shift();
        } else {
          await new Promise((r) => setTimeout(r, 400 * 2 ** next.attempts));
        }
      }
      setUnsent(queue.current.length);
    }

    draining.current = false;
    setUnsent(0);
  }, [token]);

  // Last chance to flush when the page is being hidden — the tab closing, the
  // phone locking, the student switching apps mid-module.
  useEffect(() => {
    const flush = () => {
      for (const pending of queue.current) {
        navigator.sendBeacon?.(
          `/a/${token}/answer`,
          new Blob([JSON.stringify(pending)], { type: "application/json" }),
        );
      }
    };
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, [token]);

  const answer = useCallback(
    (value: number) => {
      if (!item || !isValidResponse(item, value)) return;

      queue.current.push({
        itemId: item.id,
        value,
        // Zero means "not measured" rather than "instant" — the state before
        // the mount effect has stamped the clock. Without this guard the
        // subtraction would be against 0 and every such item would arrive at
        // the 2-minute cap, quietly poisoning flags.py's too_fast sum.
        msElapsed: shownAt.current === 0 ? 0 : clampMsElapsed(Date.now() - shownAt.current),
        attempts: 0,
      });
      setUnsent(queue.current.length);
      void drain();

      // Advance immediately. The write is in flight; making the student watch
      // it is what turns 110 taps into a chore.
      setAnswered((prev) => new Set(prev).add(item.id));
      const next = index + 1;
      if (next < items.length && isModuleStart(modules, next)) {
        setShowTransition(true);
      }
      setIndex(next);
    },
    [drain, index, item, items.length, modules],
  );

  const progress = useMemo(
    () => progressForModules(modules, answered, index),
    [modules, answered, index],
  );

  if (index >= items.length) {
    return <FinishScreen token={token} unsent={unsent} onDrain={drain} />;
  }

  if (showTransition) {
    const moduleIndex = moduleIndexForItem(modules, index);
    return (
      <main className="mx-auto flex min-h-[70vh] w-full max-w-xl flex-col justify-center gap-6 px-6 py-12">
        <p className="text-sm text-black/50 dark:text-white/50">
          Section {moduleIndex + 1} of {modules.length}
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">
          Nice work — next up:{" "}
          {MODULE_TITLE[modules[moduleIndex].code] ?? "a few more questions"}
        </h1>
        <p className="text-black/70 dark:text-white/70">
          {modules[moduleIndex].items.length} questions. Same as before: answer
          the way things actually are, not the way they ought to be.
        </p>
        <button
          type="button"
          onClick={() => setShowTransition(false)}
          className="self-start rounded bg-black px-5 py-3 text-sm font-medium text-white dark:bg-white dark:text-black"
        >
          Carry on
        </button>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-[80vh] w-full max-w-xl flex-col gap-8 px-6 py-8">
      <ProgressPath progress={progress} />

      <div className="flex flex-1 flex-col justify-center gap-8">
        <div className="flex flex-col gap-3">
          <p className="text-sm text-black/50 dark:text-white/50">
            {MODULE_PROMPT[item.instrument_code] ?? "Choose the closest answer."}
          </p>
          {/* R2: rendered exactly as transcribed. Never paraphrased, never
              "improved", never reordered. */}
          <h1 className="text-2xl font-medium tracking-tight text-balance">
            {item.text}
          </h1>
        </div>

        {widgetForItem(item) === "binary" ? (
          <BinaryAnswer item={item} onAnswer={answer} />
        ) : (
          <ScaleAnswer item={item} onAnswer={answer} />
        )}
      </div>

      <div className="flex h-6 items-center justify-between text-sm">
        {index > 0 ? (
          <button
            type="button"
            onClick={() => setIndex(index - 1)}
            className="text-black/50 underline dark:text-white/50"
          >
            ← Back
          </button>
        ) : (
          <span />
        )}
        {unsent > 0 && (
          <span className="text-xs text-black/40 dark:text-white/40">
            Saving…
          </span>
        )}
      </div>
    </main>
  );
}

function BinaryAnswer({
  item,
  onAnswer,
}: {
  item: TakerItem;
  onAnswer: (value: number) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <button
        type="button"
        onClick={() => onAnswer(item.response_min)}
        className="rounded-lg border border-black/15 px-4 py-6 text-base font-medium active:bg-black/5 dark:border-white/20 dark:active:bg-white/10"
      >
        Dislike
      </button>
      <button
        type="button"
        onClick={() => onAnswer(item.response_max)}
        className="rounded-lg border border-black/15 px-4 py-6 text-base font-medium active:bg-black/5 dark:border-white/20 dark:active:bg-white/10"
      >
        Like
      </button>
    </div>
  );
}

function ScaleAnswer({
  item,
  onAnswer,
}: {
  item: TakerItem;
  onAnswer: (value: number) => void;
}) {
  const labels = scaleLabels(item);

  // Stacked rather than a row of numbered circles: five readable labels beat
  // five digits the student has to map onto a legend, and it stays legible on a
  // narrow phone without shrinking the tap target.
  return (
    <div className="flex flex-col gap-2">
      {labels.map((label, offset) => (
        <button
          key={label}
          type="button"
          onClick={() => onAnswer(item.response_min + offset)}
          className="rounded-lg border border-black/15 px-4 py-4 text-left text-base active:bg-black/5 dark:border-white/20 dark:active:bg-white/10"
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/**
 * The progress path (§13): dots per module, not a bar and not a counter.
 *
 * At 60 items a dot each would be a grey smear on a phone, so a module's
 * segments are capped and the dots stand for proportion rather than for
 * individual questions. Nobody is counting them; they are there to show
 * movement and how many sections remain.
 */
function ProgressPath({
  progress,
}: {
  progress: ReturnType<typeof progressForModules>;
}) {
  const SEGMENTS = 12;

  return (
    <div className="flex items-center gap-3" aria-hidden="true">
      {progress.map((section) => {
        const filled = Math.round((section.answered / section.total) * SEGMENTS);
        return (
          <div key={section.code} className="flex flex-1 gap-1">
            {Array.from({ length: SEGMENTS }, (_, i) => (
              <span
                key={i}
                className={`h-1.5 flex-1 rounded-full ${
                  i < filled
                    ? "bg-black/70 dark:bg-white/70"
                    : "bg-black/10 dark:bg-white/15"
                }`}
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}

function FinishScreen({
  token,
  unsent,
  onDrain,
}: {
  token: string;
  unsent: number;
  onDrain: () => Promise<void>;
}) {
  const [state, formAction, pending] = useActionState<SubmitState, FormData>(
    submitAssessment,
    { status: "idle" },
  );

  // Everything is answered but the last few writes may still be in flight, and
  // `submit_assessment` refuses an incomplete session. Drain first so the
  // ordinary case never shows the student a failure they did not cause.
  useEffect(() => {
    void onDrain();
  }, [onDrain]);

  return (
    <main className="mx-auto flex min-h-[70vh] w-full max-w-xl flex-col justify-center gap-6 px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">
        That is everything
      </h1>
      <p className="text-black/70 dark:text-white/70">
        Thank you — that is the whole assessment. Send your answers and you are
        finished.
      </p>

      <form action={formAction} className="flex flex-col gap-3">
        <input type="hidden" name="token" value={token} />

        {state.status === "error" && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {state.message}
          </p>
        )}

        <button
          type="submit"
          disabled={pending || unsent > 0}
          className="self-start rounded bg-black px-5 py-3 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {pending ? "Sending…" : unsent > 0 ? "Saving your answers…" : "Send my answers"}
        </button>
      </form>
    </main>
  );
}

/** True when `index` is the first item of a module. */
function isModuleStart(modules: TakerModule[], index: number): boolean {
  let offset = 0;
  for (const section of modules) {
    if (index === offset) return true;
    offset += section.items.length;
  }
  return false;
}
