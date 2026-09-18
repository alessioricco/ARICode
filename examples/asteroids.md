Build a complete browser-based JavaScript videogame inspired by classic asteroid-dodging arcade games.

Requirements:

- Use vanilla JavaScript, HTML, and CSS unless the existing project uses another framework.
- Create a playable space game where the player controls a spaceship in an arena.
- The spaceship must:
  - Rotate left and right.
  - Move forward with acceleration and friction.
  - Shoot projectiles.
  - Wrap around the screen edges.
- Add asteroids that:
  - Move with varied velocities and rotations.
  - Wrap around the screen edges.
  - Split into smaller asteroids when shot.
  - Disappear when the smallest size is destroyed.
- Implement collision detection between:
  - The spaceship and asteroids.
  - Projectiles and asteroids.
- Add game states:
  - Start screen.
  - Active gameplay.
  - Player death.
  - Game over.
  - Restart.
- Add scoring, lives, and progressive difficulty.
- Include keyboard controls:
  - Arrow keys or WASD for movement.
  - Spacebar to shoot.
  - P or Escape to pause.
  - Enter to start or restart.
- Make the game responsive and playable on desktop and mobile-sized screens.
- Add touch controls for mobile if practical.
- Use a canvas for rendering unless the existing project has a strong reason to use another approach.
- Include polished visual feedback:
  - Explosion effects.
  - Screen shake or flash on impact.
  - Player thrust effect.
  - Clear score, lives, level, and pause indicators.
- Add simple sound effects using browser APIs or local assets only. The game must still work if audio is unavailable.
- Avoid external network dependencies unless they are already present in the project.

Quality requirements:

- Inspect the existing repository before making changes.
- Preserve the project’s current framework and conventions if one exists.
- Organize the game logic into clear modules or classes.
- Avoid putting all logic in one large file.
- Keep animation timing frame-rate independent using delta time.
- Prevent repeated shooting from creating unlimited projectiles.
- Handle window resizing without breaking the game.
- Do not use placeholder buttons or unfinished features.
- Add tests for important pure game logic where the project supports testing.
- Run the project’s configured build, test, lint, and type-check commands.
- Fix all syntax, build, and test failures before finishing.
- Verify manually that the game starts, controls work, collisions work, scoring works, pause works, and restart works.
- Update the README with exact installation and run commands.

Acceptance criteria:

- The game launches using the project’s documented command.
- The player can control the ship, shoot, destroy asteroids, and earn points.
- Asteroids split correctly and eventually disappear.
- Collisions cause the expected player damage or death.
- The game includes working start, pause, game-over, and restart states.
- The game works at different viewport sizes.
- No configured tests, builds, lint checks, or type checks are failing.
- The final response reports what was implemented and the actual verification commands and results.