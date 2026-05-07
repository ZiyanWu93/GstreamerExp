# Principles for presenting results in conversation

These rules are about how to *answer questions* about experimental
results clearly — the prose answer in chat, not the HTML page.
(For pages, see `PRESENTATION_PRINCIPLES.md`.)

The rules below come from corrections I kept getting wrong: switching
which question I was answering, leading with hedges, dumping numbers
before stating a claim, using defensive phrasing.

---

## 1. Answer the question that was asked
The subject of the answer must match the subject of the question.
If asked "is SCReAM better?", the answer must contain "SCReAM" and
"GCC" — not "the deadline doesn't bite."

## 2. Say which slice of the data you're answering from
Multi-dimensional data (recovery × budget × CC) supports several
distinct questions. Always name the slice before quoting numbers.
"Looking at SCReAM vs GCC, holding budget and recovery constant: …"

## 3. Lead with the direct answer
First sentence is the answer. Caveats follow. Not the other way
around. "Yes, on the things we measured. Caveats: …" — not "Well,
it depends, with caveats X and Y, but yes."

## 4. One sentence when asked for one sentence
If the user asks for one sentence, give exactly that. No
explanation paragraph attached unless requested.

## 5. State the property positively
"SCReAM delivers more frames than GCC" — not "GCC is not better
than SCReAM" or "the H4 prediction does not hold."
Negations and defensive phrasings make the reader infer the
property from what didn't happen.

## 6. Don't switch tracks mid-answer
If you start answering question A, finish A. If you want to switch
to B, say so explicitly: "switching to a different question — …"
Otherwise the reader is reading two answers without realizing it.

## 7. Numbers serve claims; they don't replace them
A claim is a sentence about behavior. Numbers verify it. Don't dump
numbers and let the reader extract the property — name the property,
then show the number.

## 8. Match the answer's length to the question's
Yes/no question → short answer. "Discuss" → fuller answer.
A direct question doesn't deserve a five-paragraph reply.

## 9. Don't pad with throat-clearing or auto-corrections
"I was overcompensating earlier — actually …" wastes time. Just
give the better answer. Acknowledge the prior mistake only if it
matters to the next decision.

## 10. When the answer is "the experiment didn't say either way," say that
Don't manufacture a verdict to be helpful. If the deadline didn't
bite, say "the deadline didn't bite — the experiment doesn't
adjudicate the prediction either way." Then propose the parameter
change that would.
