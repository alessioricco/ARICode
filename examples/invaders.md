# Task: Build a Space Invaders–style game in JavaScript for the browser

## Goal
Implement a complete, polished, playable fixed-shooter game in the style of the classic arcade game Space Invaders. It must run by opening a single HTML file in a modern browser, with no build step and no server.

## Technical constraints
- Deliver ONE self-contained file: `index.html` with inline CSS and JavaScript.
- Use vanilla JavaScript (ES6+) and the HTML5 Canvas 2D API. No frameworks, no external libraries, no external assets (images, fonts, or sound files).
- Draw all sprites procedurally (e.g., small pixel-art bitmaps defined as arrays of strings/numbers and rendered to the canvas). Design ORIGINAL sprites; do not copy the original arcade artwork.
- Generate sound effects with the Web Audio API (oscillators/noise). Audio must start only after the first user interaction, per browser autoplay rules.
- Use `requestAnimationFrame` with a delta-time-based game loop so speed is consistent across refresh rates. Cap delta time to avoid huge jumps after tab switches.
- Use a fixed logical resolution (e.g., 224×256 or 448×512) and scale the canvas to fit the window while preserving aspect ratio. Disable image smoothing for crisp pixels.

## Gameplay requirements
Player:
- A cannon at the bottom of the screen that moves left/right and cannot leave the play area.
- Fires one bullet upward; only one player bullet on screen at a time.
- Starts with 3 lives. Losing a life triggers a short explosion animation and brief respawn delay.

Invaders:
- A formation of 5 rows × 11 columns, with 3 enemy types worth different points (e.g., 30 / 20 / 10 by row).
- The formation moves horizontally as a group, steps down when any invader reaches an edge, and reverses direction.
- Two-frame walking animation that toggles with each movement step.
- The formation speeds up as invaders are destroyed (the fewer left, the faster they move).
- Invaders fire bullets downward at random intervals, only from the lowest invader in each column. Limit the number of enemy bullets on screen (e.g., max 3).
- If the formation reaches the player's row, the game is over.

Mystery ship:
- A bonus ship occasionally crosses the top of the screen. Shooting it awards a random bonus (e.g., 50–300 points), shown briefly where it was hit.

Shields:
- 4 destructible bunkers between the player and the invaders.
- Bullets from either side erode them pixel-by-pixel (or in small chunks). Invaders touching the bunkers also erase them.

Collisions:
- Player bullet vs. invader, mystery ship, bunker, and enemy bullet (bullets cancel each other out).
- Enemy bullet vs. player and bunker.
- Use axis-aligned bounding boxes, with pixel-level checks for bunkers.

Progression:
- Clearing a wave starts the next wave, with the formation starting slightly lower and/or moving faster.
- Award an extra life at 1,500 points (once).

## UI and game states
Implement a simple state machine with these states:
- TITLE: game name, point values of each enemy type, "Press Enter/Space to start".
- PLAYING: score (top left), high score (top center), lives remaining (bottom), current wave.
- PAUSED: toggled with P or Escape; show an overlay.
- GAME_OVER: final score, "Press Enter to play again".
Persist the high score in localStorage (wrap access in try/catch so the game still works if storage is unavailable).

## Controls
- Left/Right arrow keys or A/D: move.
- Space: fire.
- P or Escape: pause.
- Enter: start/restart.
- Prevent default browser scrolling for arrow keys and Space.
- Bonus: simple on-screen touch buttons (left, right, fire) that appear on touch devices.

## Audio
Short synthesized sounds for: player shot, invader destroyed, player destroyed, mystery ship (looping tone while on screen), and the classic 4-note descending "march" bass that plays with each formation step and speeds up with it. Include a mute toggle (M key).

## Code structure
Even though it's one file, organize the code clearly:
- Constants/config block at the top (speeds, sizes, colors, point values) so tuning is easy.
- Separate classes or modules for Player, InvaderFormation, Invader, Bullet, Bunker, MysteryShip, SoundManager, InputHandler, and Game.
- Separate `update(dt)` and `render(ctx)` phases.
- Comment non-obvious logic (formation stepping, bunker erosion, speed scaling).

## Acceptance criteria
Before finishing, verify that:
1. Opening index.html directly in Chrome/Firefox starts at the title screen with no console errors.
2. A full game can be played from start to game over, and restarting resets everything correctly.
3. The formation speeds up as invaders die, and a new wave spawns when all are cleared.
4. Bunkers visibly erode and eventually break through.
5. The high score persists after reloading the page.
6. Game speed feels the same on 60Hz and 120Hz+ displays.
7. The game remains playable and correctly scaled when the window is resized.

## Deliverable
Return the complete `index.html` file, followed by a brief summary of the controls and any config values worth tweaking.