Build a polished, interactive terminal-based ASCII version of the Tower of Hanoi in Python.

The goal is to create a small but well-engineered CLI application that lets a user visually play and solve the Tower of Hanoi puzzle directly in their terminal.

Requirements:

1. Core gameplay

* Implement the classic Tower of Hanoi with three rods: A, B, and C.
* Allow the user to choose the number of disks at startup.
* Support at least 1–12 disks.
* Initialize all disks on rod A, ordered largest at the bottom to smallest at the top.
* The objective is to move the entire stack from A to C.
* Enforce the standard rules:
    * Only one disk can be moved at a time.
    * Only the top disk of a rod can be moved.
    * A larger disk cannot be placed on top of a smaller disk.
* Detect when the puzzle is solved and display a clear completion message.
* Track and display the number of moves.
* Display the theoretical minimum number of moves: 2^n - 1.

2. Interactive terminal UI
    Create an attractive ASCII representation of the puzzle.

For example, the terminal should visually resemble:

    |               |               |
   ===              |               |
  =====             |               |
 =======            |               |
=========            |               |

⸻

   A               B               C

The exact design is up to you, but prioritize readability.

The UI should:

* Dynamically resize the disk representation according to the number of disks.
* Keep the three rods visually aligned.
* Clearly distinguish different disks using ASCII characters, width, or another terminal-friendly technique.
* Redraw the puzzle after every move.
* Show the current move count and minimum required moves.
* Show helpful instructions below the puzzle.

3. User interaction
    Provide a simple command interface such as:

A C
A B
B C

where the first letter is the source rod and the second is the destination rod.

Also support useful commands such as:

help
solve
reset
quit

The command parser should be forgiving:

* Accept lowercase or uppercase input.
* Ignore unnecessary whitespace.
* Provide useful error messages for invalid commands.
* Never crash because of malformed user input.

4. Automatic solver
    Implement a recursive Tower of Hanoi solver.

When the user enters:

solve

the application should automatically solve the puzzle step-by-step.

Requirements:

* Animate the moves in the terminal.
* Add a small configurable delay between moves.
* Update the ASCII board after every move.
* Keep the move counter synchronized with the animation.
* Allow the user to configure or disable the animation delay if practical.

5. Architecture
    Keep the implementation clean and maintainable.

Prefer a structure along these lines:

hanoi.py

with clear separation between:

* Puzzle/game state
* Move validation
* Hanoi solving algorithm
* ASCII rendering
* User input / command handling

Use classes where they make the code clearer, but don’t over-engineer the project.

For example, a HanoiGame class could own the board state and operations, while separate functions/classes handle rendering and solving.

6. Terminal behavior

* Use only the Python standard library unless there is a compelling reason otherwise.
* Do not require curses, external packages, or platform-specific dependencies unless absolutely necessary.
* It should work on macOS, Linux, and Windows terminals where reasonably possible.
* Use ANSI escape sequences only where useful, and provide graceful behavior if the terminal doesn’t support them.
* Avoid flooding the terminal with output. Redraw the board cleanly between moves when possible.
* Handle Ctrl+C gracefully and exit cleanly.

7. User experience
    Make the application feel like a small polished terminal game rather than a coding exercise.

At startup, display a short title such as:

╔══════════════════════════════════╗
║       TOWER OF HANOI             ║
╚══════════════════════════════════╝

Then ask:

Number of disks [3]:

Validate the input and explain invalid values clearly.

After initialization, display the board and instructions.

For example:

Moves: 0
Minimum: 7

───────────────────────────────────

        |               |               |
       ===              |               |
      =====             |               |
     =======            |               |

─────────────────────────────────────────────
A               B               C

Move a disk: A C
Commands: help | solve | reset | quit

8. Quality requirements

* Write idiomatic Python.
* Add type hints.
* Add docstrings to important classes/functions.
* Keep functions reasonably small.
* Avoid global mutable state.
* Validate all user input.
* Make the game logic independently testable.
* Include unit tests for:
    * Valid moves
    * Invalid moves
    * Moving from an empty rod
    * Attempting to move a non-top disk
    * Attempting to place a larger disk on a smaller disk
    * Solved-state detection
    * Minimum move calculation
    * Automatic solver
* Include a README explaining how to run the application and play the game.

9. Optional polish
    If straightforward, add:

* Terminal colors using ANSI escape sequences, with a --no-color option.
* A --disks N command-line argument.
* A --delay SECONDS argument for solver animation.
* A progress indicator while automatically solving.
* A “new best score” or efficiency indicator based on whether the user solved the puzzle in the minimum number of moves.
* A hint command that suggests a valid next move.
* A step command during automatic solving if you can implement it cleanly.

Important implementation constraints:

* The Tower of Hanoi rules must be implemented in the game state itself, not merely enforced by the UI.
* The automatic solver must use the same move mechanism as manual gameplay so that validation and move counting cannot diverge.
* Do not hard-code the visual layout for a particular number of disks.
* Keep the core game logic independent from terminal rendering so it can be tested without a terminal.

Before finishing:

1. Run the application.
2. Test manual moves.
3. Test invalid moves.
4. Test solve.
5. Test reset.
6. Test quit and Ctrl+C.
7. Run the unit test suite.
8. Fix any issues you discover.

Deliver the complete working project, including the Python implementation, tests, and README. Do not merely provide pseudocode or an explanation.