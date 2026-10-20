import { test } from "node:test";
import assert from "node:assert/strict";
import { relayIfBlocked } from "../../custom_components/elevenagents/card/logic.js";

// A stand-in for a MediaStream.
function fakeStream(tracks = []) {
  return {
    tracks: [...tracks],
    getAudioTracks() {
      return this.tracks;
    },
    addTrack(track) {
      this.tracks.push(track);
    },
  };
}

// A stand-in for an audio element. `blocked` makes play() reject.
function fakeAudio({ blocked = false, srcObject = null } = {}) {
  return {
    blocked,
    srcObject,
    muted: false,
    plays: 0,
    play() {
      this.plays += 1;
      return this.blocked ? Promise.reject(new Error("NotAllowedError")) : Promise.resolve();
    },
  };
}

test("an element that plays is left alone", async () => {
  const element = fakeAudio({ srcObject: fakeStream(["agent"]) });
  const voice = fakeAudio({ srcObject: fakeStream() });
  assert.equal(await relayIfBlocked(element, voice), false);
  assert.equal(element.muted, false);
  assert.deepEqual(voice.srcObject.tracks, []);
});

test("a blocked element adds its tracks to the playing voice stream", async () => {
  const element = fakeAudio({ blocked: true, srcObject: fakeStream(["agent"]) });
  const voice = fakeAudio({ srcObject: fakeStream() });
  assert.equal(await relayIfBlocked(element, voice), true);
  assert.equal(element.muted, true);
  assert.deepEqual(voice.srcObject.tracks, ["agent"]);
  assert.equal(voice.plays, 0);
});

test("without a voice stream or tracks nothing is relayed", async () => {
  const blocked = () => fakeAudio({ blocked: true, srcObject: fakeStream(["agent"]) });
  assert.equal(await relayIfBlocked(blocked(), null), false);
  assert.equal(await relayIfBlocked(blocked(), fakeAudio()), false);
  const voice = fakeAudio({ srcObject: fakeStream() });
  assert.equal(await relayIfBlocked(fakeAudio({ blocked: true, srcObject: fakeStream() }), voice), false);
  assert.deepEqual(voice.srcObject.tracks, []);
});
