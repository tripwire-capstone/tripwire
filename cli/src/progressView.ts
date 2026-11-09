// A block of lines that updates in place while a long step runs:
//   ◆  Building Laminar · 4m 12s (usually 8–10 minutes)
//   │    ✓ Network                1m 40s
//   │    ◒ Database               4m 12s   usually the slowest
// @clack/prompts has no multi-line live area (its spinner is one line), so this redraws the
// block itself with cursor-up + erase. When output isn't a terminal it prints one line per
// finished row instead.
import { isTTY, S_BAR, S_STEP_ACTIVE, S_STEP_ERROR, S_STEP_SUBMIT, unicodeOr } from "@clack/prompts";
import type { Writable } from "node:stream";
import { stripVTControlCharacters, styleText } from "node:util";
import {
  groupSeconds,
  isLongerThanUsual,
  statusOf,
  type GroupStatus,
  type Progress,
} from "./progressGroups.js";
import { formatDuration } from "./tfEvents.js";

export type RowStatus = GroupStatus;

export interface Row {
  name: string;
  status: RowStatus;
  seconds: number;
  // Shown after the time, e.g. a note or a phase
  detail?: string;
}

export interface Block {
  title: string;
  status: "running" | "done" | "failed";
  seconds: number;
  // e.g. "usually 8–10 minutes"; shown while running
  estimate?: string;
  rows: Row[];
  // A calm line under the rows, e.g. when it takes longer than usual
  warning?: string;
}

const FRAMES = [unicodeOr("◒", "•"), unicodeOr("◐", "o"), unicodeOr("◓", "O"), unicodeOr("◑", "0")];
const DONE = unicodeOr("✓", "√");
const FAILED = unicodeOr("✗", "x");
const PENDING = unicodeOr("·", ".");
const NAME_WIDTH = 20;

const LONGER_THAN_USUAL = "Taking longer than usual; AWS is sometimes slow. Still working…";

function gray(text: string): string {
  return styleText("gray", text);
}

function rowSymbol(status: RowStatus, frame: number): string {
  switch (status) {
    case "done":
      return styleText("green", DONE);
    case "failed":
      return styleText("red", FAILED);
    case "running":
      return styleText("magenta", FRAMES[frame % FRAMES.length]);
    case "pending":
    case "waiting":
      return styleText("dim", PENDING);
  }
}

// Cuts a line to the terminal width; a wrapped line would break the cursor math on redraw
export function fitToWidth(line: string, columns: number): string {
  const plain = stripVTControlCharacters(line);
  if (plain.length <= columns) return line;
  return `${plain.slice(0, Math.max(0, columns - 1))}…`;
}

// The name column fits the longest name in the block (at least NAME_WIDTH), so times line up
function nameWidth(rows: Row[]): number {
  return Math.max(NAME_WIDTH, ...rows.map((row) => row.name.length));
}

function rowLine(row: Row, frame: number, width = NAME_WIDTH): string {
  // Waiting rows show no time: the group isn't doing anything itself
  const time = row.status === "pending" || row.status === "waiting" ? "" : formatDuration(row.seconds);
  const detail = row.detail ? `   ${styleText("dim", row.detail)}` : "";
  return `${gray(S_BAR)}    ${rowSymbol(row.status, frame)} ${row.name.padEnd(width)} ${time.padStart(7)}${detail}`;
}

// The block as lines; frame picks the spinner frame
export function renderBlock(block: Block, frame: number, columns = 80): string[] {
  let header: string;
  if (block.status === "running") {
    const estimate = block.estimate ? ` (${block.estimate})` : "";
    header = `${styleText("cyan", S_STEP_ACTIVE)}  ${block.title} · ${formatDuration(block.seconds)}${estimate}`;
  } else if (block.status === "done") {
    header = `${styleText("green", S_STEP_SUBMIT)}  ${block.title} ${gray(`[${formatDuration(block.seconds)}]`)}`;
  } else {
    header = `${styleText("red", S_STEP_ERROR)}  ${block.title} stopped ${gray(`[${formatDuration(block.seconds)}]`)}`;
  }
  const width = nameWidth(block.rows);
  const lines = [gray(S_BAR), header, ...block.rows.map((row) => rowLine(row, frame, width))];
  if (block.warning) lines.push(`${gray(S_BAR)}    ${styleText("yellow", block.warning)}`);
  return lines.map((line) => fitToWidth(line, columns));
}

// A Terraform apply's progress as a block
export function progressBlock(
  progress: Progress,
  options: { title: string; estimate?: string; usualSeconds?: number; now: number; status?: Block["status"] },
): Block {
  const { title, estimate, usualSeconds, now } = options;
  const status = options.status ?? "running";
  const rows = progress.groups.map((group) => {
    const groupStatus = statusOf(group);
    const detail = groupStatus === "waiting" ? "waiting" : groupStatus === "running" ? group.note : undefined;
    return {
      name: group.name,
      status: groupStatus,
      seconds: groupSeconds(group, now),
      ...(detail ? { detail } : {}),
    };
  });
  const slow = status === "running" && usualSeconds !== undefined && isLongerThanUsual(progress, now, usualSeconds);
  return {
    title,
    status,
    seconds: (now - progress.startedAt) / 1000,
    ...(status === "running" && estimate ? { estimate } : {}),
    rows,
    ...(slow ? { warning: LONGER_THAN_USUAL } : {}),
  };
}

const HIDE_CURSOR = "\x1b[?25l";
const SHOW_CURSOR = "\x1b[?25h";
const ERASE_DOWN = "\x1b[0J";

function cursorUp(count: number): string {
  return count > 0 ? `\x1b[${count}A` : "";
}

export interface ProgressDisplay {
  // Draws the final state and stops updating
  stop(): void;
}

// Redraws the block in place, about 8 times a second (spinner) — for terminals
function liveDisplay(render: () => Block, output: Writable & { columns?: number }): ProgressDisplay {
  let drawn = 0;
  let frame = 0;
  const draw = () => {
    const lines = renderBlock(render(), frame++, output.columns ?? 80);
    output.write(`${cursorUp(drawn)}${ERASE_DOWN}${lines.join("\n")}\n`);
    drawn = lines.length;
  };
  const showCursor = () => output.write(SHOW_CURSOR);
  // Ctrl-C: give the cursor back, then exit the way Node would have
  const onInterrupt = () => {
    showCursor();
    process.kill(process.pid, "SIGINT");
  };

  output.write(HIDE_CURSOR);
  process.once("SIGINT", onInterrupt);
  process.once("exit", showCursor);
  draw();
  const ticker = setInterval(draw, 125);

  return {
    stop() {
      clearInterval(ticker);
      draw();
      showCursor();
      process.removeListener("SIGINT", onInterrupt);
      process.removeListener("exit", showCursor);
    },
  };
}

// Prints the header, then a line as each row finishes — for CI and piped output
function plainDisplay(render: () => Block, output: Writable): ProgressDisplay {
  const printed = new Set<string>();
  const first = renderBlock(render(), 0, Number.MAX_SAFE_INTEGER);
  output.write(`${first.slice(0, 2).map(stripVTControlCharacters).join("\n")}\n`);
  let warned = false;

  const check = () => {
    const block = render();
    for (const row of block.rows) {
      if ((row.status === "done" || row.status === "failed") && !printed.has(row.name)) {
        printed.add(row.name);
        output.write(`${stripVTControlCharacters(rowLine({ ...row, detail: undefined }, 0, nameWidth(block.rows)))}\n`);
      }
    }
    if (block.warning && !warned) {
      warned = true;
      output.write(`${S_BAR}    ${block.warning}\n`);
    }
  };
  const ticker = setInterval(check, 1000);

  return {
    stop() {
      clearInterval(ticker);
      check();
      const header = renderBlock(render(), 0, Number.MAX_SAFE_INTEGER)[1];
      output.write(`${stripVTControlCharacters(header)}\n`);
    },
  };
}

// Shows a live block until stop(); render is called for each redraw
export function showProgress(
  render: () => Block,
  output: Writable & { columns?: number } = process.stdout,
): ProgressDisplay {
  return isTTY(output) ? liveDisplay(render, output) : plainDisplay(render, output);
}
