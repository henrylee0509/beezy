# Voice-over script (3:00)

Timed to `Beezy_demo.mp4`. Each block fits its scene at a relaxed pace (about 2.4 words per second), so there's room to breathe. Read the **bold** cue, then speak while that scene is on screen.

> **Before you record:** if your copy of the video shows the amber "Mock AI mode" banner, use the *mock wording* where a line offers it, so the narration matches the screen. Re-recording the footage with your API key (see `README.md`) makes every line accurate as written.

## Intro and problem

**0:00 · Title**
Hi, I'm Henry. This is Beezy: an AI operations team for small businesses, built on Meta's Muse Spark.

**0:09 · Six systems**
A cafe owner like Mia runs her business across six tools that never talk to each other: a stock spreadsheet, supplier emails, a payroll calculator, a contract folder. So she finds out about problems after they've already cost her money.

**0:23 · The idea**
Beezy gives her six AI departments. They detect problems, prepare the work by comparing suppliers and drafting orders and payroll, and then they wait. Nothing that spends money happens without her approval.

## Architecture

**0:35 · Architecture**
Under the hood, an Executive Assistant on Muse Spark plans each request and consults specialist agents in parallel. Each agent calls typed tools backed by deterministic code, so the model makes the decisions but never does the maths. Every draft lands in an approval queue, and a verifier checks every figure in every answer.

## Live demo

**0:54 · "Let's run a cafe."**
Let's see it running.

**0:57 · Overview**
Mia signs in to a morning briefing from the Business Intelligence agent. Every figure in it has been checked against her records. Below it are live KPIs, alerts and forecasts across the business.

**1:11 · Health check**
She asks for a health check. The Executive Assistant decides which departments it needs, and you can watch each one work through real tool calls. She gets one answer covering stock, cash, payroll and contracts, with every number traced back to her data.

**1:31 · Restock**
Now she asks it to act: restock everything below twenty percent before it runs out. The Procurement agent drafts three purchase orders, but only drafts them. She approves the dairy order right here in the chat.

**1:47 · Supplier choice**
Why Highland Roasters and not the cheaper supplier? The cheaper one can't deliver before the beans run out in four days. That comparison is plain, testable code.

**1:58 · Contract upload**
She uploads a new pastry supplier contract. The Contract agent pulls out the term, the payment terms and the forty-five-day notice clause, with page references, and schedules reminders.

**2:11 · Payroll**
Payroll is calculated in code, never by the model, and it flags problems before payday: one missing timesheet, one missing bank account.

**2:20 · Automation**
Automations react on their own. When oat milk drops below twenty percent, a purchase order is drafted for approval straight away.

**2:29 · Audit trail**
And every agent action is recorded in the audit trail.

## Conclusion and final thoughts

**2:34 · Built to be trusted**
That's what makes it trustworthy: verified figures, human approval for anything involving money, role-aware agents, and fifty-three automated tests, including the Muse Spark tool-calling path.

**2:45 · What's next**
Next come live point-of-sale and bank feeds, orders that go straight to suppliers, and professionally verified payroll packs.

**2:53 · Close**
Detect. Prepare. You approve. That's Beezy. Thanks for watching.

## Mock wording

Use these lines instead only if your footage shows "Mock AI mode":

- **0:57 · Overview:** "Mia signs in to a morning briefing that pulls the key numbers from every department, each one checked against her records."
- **1:11 · Health check:** "She asks for a health check. The Executive Assistant decides which departments it needs, and you can watch each one work through real tool calls on her data."

## Recording tips

- Record in a quiet room, about a hand's width from the mic, in one take per section. Pauses between sections are easy to trim.
- To lay the narration over the video, replacing the silent track: `ffmpeg -i Beezy_demo.mp4 -i voiceover.m4a -map 0:v -map 1:a -c:v copy -c:a aac -shortest Beezy_final.mp4`
