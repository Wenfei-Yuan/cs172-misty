from __future__ import annotations

BOOT_FACE = "e_Joy.jpg"
READING_FACE = "e_EyesWide.jpg"
SPEAKING_FACE = "e_ContentDefault.jpg"
DISTRACTION_FACE = "e_Concerned.jpg"
CLOSE_FACE = "e_Joy.jpg"


def show_image(misty, filename: str) -> None:
    try:
        misty.perform_action("image_show", {"FileName": filename})
    except Exception as exc:
        print(f"Error displaying image {filename}: {exc}")


def arm_gesture(misty, preset: str) -> None:
    try:
        if preset == "wave":
            misty.perform_action(
                "arms_move",
                {
                    "LeftArmPosition": -80,
                    "RightArmPosition": -80,
                    "LeftArmVelocity": 50,
                    "RightArmVelocity": 50,
                },
            )
    except Exception as exc:
        print(f"Error performing gesture {preset}: {exc}")
