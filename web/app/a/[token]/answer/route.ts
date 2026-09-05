import { NextResponse, type NextRequest } from "next/server";

import { createAdminClient } from "@/lib/supabase/admin";
import { clampMsElapsed } from "@/lib/taker";

import { hashToken } from "../queries";

/**
 * One answer (plan.md §13, M6).
 *
 * A route handler rather than a server action, which is the one place this
 * milestone departs from the pattern the rest of the app follows.
 *
 * Server actions are the right tool for consent and submit — they are
 * navigations, and they get CSRF protection and progressive enhancement for
 * free. They are the wrong tool here. Next queues actions sequentially per
 * client and each response carries a re-render payload for the current route;
 * across ~110 rapid taps on a phone that is a queue the student can outrun and
 * a great deal of payload nobody reads. A route handler answers in a few dozen
 * bytes, runs in parallel, and can be reached by `sendBeacon` when the page is
 * being hidden — which is what makes "the connection died mid-module" lose one
 * answer rather than several.
 *
 * The trade is that route handlers get none of an action's CSRF protection, so
 * the Origin check below is put back by hand. It adds no real defence — the
 * token in the URL is the credential, and an attacker holding it does not need
 * a victim's browser — but a cross-site page should not be able to quietly
 * corrupt an assessment it can guess the link to.
 */

const MAX_BODY_BYTES = 1024;

export async function POST(
  request: NextRequest,
  { params }: RouteContext<"/a/[token]/answer">,
) {
  const origin = request.headers.get("origin");
  if (origin && origin !== request.nextUrl.origin) {
    return NextResponse.json({ error: "bad origin" }, { status: 403 });
  }

  const { token } = await params;
  if (!token || token.length > 128) {
    return NextResponse.json({ error: "bad token" }, { status: 400 });
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) {
    return NextResponse.json({ error: "body too large" }, { status: 413 });
  }

  let body: { itemId?: unknown; value?: unknown; msElapsed?: unknown };
  try {
    body = JSON.parse(raw);
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }

  const itemId = typeof body.itemId === "string" ? body.itemId : null;
  const value = typeof body.value === "number" ? body.value : null;

  if (!itemId || value === null || !Number.isInteger(value)) {
    return NextResponse.json({ error: "bad answer" }, { status: 400 });
  }

  const admin = createAdminClient();

  // No participant id, no session id — the token hash resolves both inside
  // Postgres. `record_response` validates the value against that item's own
  // response_min/response_max and refuses once the session is submitted, so the
  // range check is next to the data rather than trusted from the browser.
  const { error } = await admin.rpc("record_response", {
    p_token_hash: hashToken(token),
    p_item_id: itemId,
    p_value: value,
    p_ms_elapsed: clampMsElapsed(
      typeof body.msElapsed === "number" ? body.msElapsed : 0,
    ),
  });

  if (error) {
    // 500 rather than 400 even for a rejected value: the client retries on
    // failure, and everything it can legitimately send has already been checked
    // by `isValidResponse` before it left the page. A rejection here means the
    // two disagree, which is our bug to see in the logs, not the student's to
    // read on screen.
    return NextResponse.json({ error: "not recorded" }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
