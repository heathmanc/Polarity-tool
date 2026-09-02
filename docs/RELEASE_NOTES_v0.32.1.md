# Pole Position v0.32.1

Fixes the crop that ADD TO ML TRAINING takes from a rejected part, and shows it
to the technician before they label it.

## The crop was the search area, not the terminal top

Adding a reject to the training set cropped the **terminal polygon**. That is
the locator's *search area* — deliberately larger than the post so the terminal
can be found inside it — so every sample added this way carried case,
background, and often part of the other terminal. Training on those teaches the
classifier about everything except the thing it is supposed to read.

The crop now comes from the **marking polygon**: the taught circle on the metal
terminal top, mapped into the frame that was graded. That is what the
classifier is trained on and what it is run on, so a sample added from Failure
Review is now the same contract as one captured on the ML Training page. The
recorded ROI shape travels with it, so a circle stays a circle.

There is deliberately no fallback. A record with no marking outline stored is
refused with a message pointing at the ML Training page, rather than quietly
reverting to the search area.

## You can see what you are labelling

The label dialog now shows the actual crop beside each terminal's choice — the
marking crop the classifier itself was given. Labelling is a judgement about an
image, so the image belongs on screen. The station's own reading stays beside
it as context, and nothing is preselected, as before.

## Action required if you already used this

Samples added from Failure Review before this release used the wrong crop and
should be removed. They are tagged `failure_review:<inspection id>`:

1. **ML Training → review step**
2. Filter the family list by the `failure_review:` tag
3. Remove those samples

Then re-add the parts from Failure Review, which will now take the correct crop.
A model trained on the old samples should be retrained after clearing them.

## Upgrade notes

No data, configuration, or PLC contract change.
