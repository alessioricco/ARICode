Build a complete, playable browser game: an original maze-chase arcade game inspired by the classic genre.

First inspect the repository and follow its existing framework, package manager, conventions, and test commands. If this is an empty project, choose a lightweight browser game setup and document the choice. Implement the actual game as the first screen, not a landing page.

Originality and assets
- Do not use the Pac-Man name, characters, logos, exact maze layout, signature sound effects, or copied artwork.
- Create an original title, visual identity, maze layout, and enemy designs.
- Prefer assets created in code or original assets. Do not download third-party assets unless their license permits this use; preserve attribution and license notices when required.
- Do not use remote assets that could make the game fail when offline.

Gameplay
- The player navigates a maze, collects items, avoids enemies, and wins by collecting all required items.
- Include a start state, active play, pause/resume, win state, and game-over state.
- Provide clear controls: arrow keys and WASD for movement, with a visible pause control and a keyboard shortcut.
- Movement should feel responsive and obey the maze walls. Handle turns predictably, including turns requested just before reaching an intersection.
- Enemies should move through the maze and pursue or intercept the player using understandable, testable behavior. Avoid enemies becoming stuck or passing through walls.
- Include lives, score, and a way to restart. Update the interface immediately when these change.
- Add a brief invulnerability period after losing a life so the player cannot lose multiple lives instantly.
- Make the game playable at desktop and mobile sizes. On touch devices, provide usable directional controls.
- Include sound only if it can be implemented cleanly with original or generated tones. Provide a mute toggle and do not autoplay sound before user interaction.

Implementation quality
- Keep game logic separate from rendering and input handling so core rules can be tested without a browser.
- Use stable maze-cell dimensions and responsive scaling. Keep important UI visible without overlapping the playfield.
- Avoid unnecessary dependencies. If using a game framework, follow the repository’s existing stack and document the reason.
- Handle browser resizing and page focus changes sensibly. Pausing should prevent the game state from advancing.
- Make the interface accessible: semantic controls, visible focus, readable contrast, and an accessible name/status for key controls and game state.
- Do not leave placeholder buttons, TODOs, mocked gameplay, or nonfunctional controls.

Tests and verification
- Add unit tests for maze collision, movement, item collection, scoring, life loss, enemy movement, win/loss conditions, pause behavior, and restart behavior.
- Add browser-level tests using the repository’s existing browser test setup if present. Otherwise, add a suitable lightweight browser test setup and document how to run it.
- Browser tests should verify that the game loads, starts, responds to keyboard input, respects walls, updates score, can be paused and restarted, and reaches a terminal state under a deterministic test setup.
- Make randomness and timers controllable in tests where needed; do not make tests depend on timing luck.
- Run the project’s tests, lint, typecheck, and build commands when available. Fix failures caused by this implementation and report the actual results.
- Launch the game locally and inspect it in a browser at desktop and mobile viewport sizes. Check that the game is visible, controls work, and text/UI do not overlap.

Documentation
- Add or update README.md with prerequisites, install steps, development/run commands, test commands, controls, and a short description of the game.
- Keep documentation aligned with the commands that actually work.

Completion criteria
- The game can be started and played from the documented command.
- All required gameplay states and controls work.
- Tests cover the core rules and important browser interactions.
- No known test, lint, typecheck, or build failures introduced by this change remain.
- Finish with a concise summary of the game, how to run it, and the verification commands/results. Do not claim a check passed unless you ran it and saw it pass.