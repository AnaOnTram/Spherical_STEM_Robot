"""STEM content manager for educational content."""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LessonStep:
    """Single step in a lesson."""
    step_id: str
    title: str
    content: str
    display_image: Optional[str] = None  # Path to E-Ink image
    audio_file: Optional[str] = None     # Path to audio narration
    gesture_trigger: Optional[str] = None  # Gesture to advance
    duration: float = 0.0                # Auto-advance duration (0 = manual)


@dataclass
class Lesson:
    """Educational lesson content."""
    lesson_id: str
    title: str
    description: str
    category: str  # "math", "science", "language", "art"
    age_group: str  # "3-4", "4-5", "5-6"
    steps: list[LessonStep] = field(default_factory=list)
    thumbnail: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Lesson":
        """Create Lesson from dictionary."""
        steps = [LessonStep(**step) for step in data.get("steps", [])]
        return cls(
            lesson_id=data["lesson_id"],
            title=data["title"],
            description=data.get("description", ""),
            category=data.get("category", "general"),
            age_group=data.get("age_group", "3-6"),
            steps=steps,
            thumbnail=data.get("thumbnail"),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "age_group": self.age_group,
            "steps": [
                {
                    "step_id": step.step_id,
                    "title": step.title,
                    "content": step.content,
                    "display_image": step.display_image,
                    "audio_file": step.audio_file,
                    "gesture_trigger": step.gesture_trigger,
                    "duration": step.duration,
                }
                for step in self.steps
            ],
            "thumbnail": self.thumbnail,
        }


class ContentManager:
    """Manages educational content and lessons."""

    def __init__(self, content_dir: Optional[str] = None):
        self.content_dir = Path(content_dir) if content_dir else Path(__file__).parent / "assets"
        self._lessons: dict[str, Lesson] = {}
        self._loaded = False

    def load_content(self) -> bool:
        """Load all content from content directory.

        Returns:
            True if content loaded successfully
        """
        try:
            lessons_dir = self.content_dir / "lessons"
            if not lessons_dir.exists():
                logger.warning(f"Lessons directory not found: {lessons_dir}")
                lessons_dir.mkdir(parents=True, exist_ok=True)
                self._create_sample_lessons(lessons_dir)

            # Load all JSON lesson files
            for lesson_file in lessons_dir.glob("*.json"):
                try:
                    with open(lesson_file, "r") as f:
                        data = json.load(f)
                        lesson = Lesson.from_dict(data)
                        self._lessons[lesson.lesson_id] = lesson
                        logger.debug(f"Loaded lesson: {lesson.title}")
                except Exception as e:
                    logger.error(f"Failed to load lesson {lesson_file}: {e}")

            self._loaded = True
            logger.info(f"Loaded {len(self._lessons)} lessons")
            return True

        except Exception as e:
            logger.error(f"Failed to load content: {e}")
            return False

    def _create_sample_lessons(self, lessons_dir: Path) -> None:
        """Create sample lesson files."""
        sample_lessons = [
            {
                "lesson_id": "counting_1_10",
                "title": "Counting 1 to 10",
                "description": "Learn to count from 1 to 10 with fun animations",
                "category": "math",
                "age_group": "3-4",
                "steps": [
                    {
                        "step_id": "intro",
                        "title": "Let's Count!",
                        "content": "Hello! Today we're going to learn to count from 1 to 10!",
                        "gesture_trigger": "open_palm",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "count_1",
                        "title": "Number 1",
                        "content": "This is the number 1. Hold up one finger!",
                        "gesture_trigger": "pointing_up",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "count_2",
                        "title": "Number 2",
                        "content": "This is the number 2. Hold up two fingers!",
                        "gesture_trigger": "peace",
                        "duration": 5.0,
                    },
                ],
            },
            {
                "lesson_id": "colors_basic",
                "title": "Basic Colors",
                "description": "Learn about primary colors",
                "category": "art",
                "age_group": "3-4",
                "steps": [
                    {
                        "step_id": "intro",
                        "title": "Colors Around Us",
                        "content": "Colors are everywhere! Let's learn about them.",
                        "gesture_trigger": "open_palm",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "red",
                        "title": "Red",
                        "content": "Red is the color of apples and fire trucks!",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "blue",
                        "title": "Blue",
                        "content": "Blue is the color of the sky and the ocean!",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "yellow",
                        "title": "Yellow",
                        "content": "Yellow is the color of the sun and bananas!",
                        "duration": 5.0,
                    },
                ],
            },
            {
                "lesson_id": "shapes_basic",
                "title": "Basic Shapes",
                "description": "Learn about circles, squares, and triangles",
                "category": "math",
                "age_group": "3-4",
                "steps": [
                    {
                        "step_id": "intro",
                        "title": "Shape Explorer",
                        "content": "Let's discover shapes together!",
                        "gesture_trigger": "thumbs_up",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "circle",
                        "title": "Circle",
                        "content": "A circle is round like a ball or the sun!",
                        "gesture_trigger": "ok",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "square",
                        "title": "Square",
                        "content": "A square has four equal sides, like a window!",
                        "duration": 5.0,
                    },
                    {
                        "step_id": "triangle",
                        "title": "Triangle",
                        "content": "A triangle has three sides, like a slice of pizza!",
                        "duration": 5.0,
                    },
                ],
            },
        ]

        for lesson_data in sample_lessons:
            lesson_file = lessons_dir / f"{lesson_data['lesson_id']}.json"
            with open(lesson_file, "w") as f:
                json.dump(lesson_data, f, indent=2)
            logger.info(f"Created sample lesson: {lesson_data['title']}")

    def get_lesson(self, lesson_id: str) -> Optional[Lesson]:
        """Get lesson by ID."""
        if not self._loaded:
            self.load_content()
        return self._lessons.get(lesson_id)

    def list_lessons(
        self,
        category: Optional[str] = None,
        age_group: Optional[str] = None,
    ) -> list[Lesson]:
        """List available lessons.

        Args:
            category: Filter by category
            age_group: Filter by age group

        Returns:
            List of matching lessons
        """
        if not self._loaded:
            self.load_content()

        lessons = list(self._lessons.values())

        if category:
            lessons = [l for l in lessons if l.category == category]

        if age_group:
            lessons = [l for l in lessons if l.age_group == age_group]

        return lessons

    def get_categories(self) -> list[str]:
        """Get list of available categories."""
        if not self._loaded:
            self.load_content()
        return list(set(l.category for l in self._lessons.values()))

    def get_age_groups(self) -> list[str]:
        """Get list of available age groups."""
        if not self._loaded:
            self.load_content()
        return list(set(l.age_group for l in self._lessons.values()))

    def add_lesson(self, lesson: Lesson, save: bool = True) -> None:
        """Add or update a lesson.

        Args:
            lesson: Lesson to add
            save: Whether to save to file
        """
        self._lessons[lesson.lesson_id] = lesson

        if save:
            lessons_dir = self.content_dir / "lessons"
            lessons_dir.mkdir(parents=True, exist_ok=True)

            lesson_file = lessons_dir / f"{lesson.lesson_id}.json"
            with open(lesson_file, "w") as f:
                json.dump(lesson.to_dict(), f, indent=2)

    def delete_lesson(self, lesson_id: str) -> bool:
        """Delete a lesson.

        Args:
            lesson_id: Lesson ID to delete

        Returns:
            True if deleted successfully
        """
        if lesson_id not in self._lessons:
            return False

        del self._lessons[lesson_id]

        lesson_file = self.content_dir / "lessons" / f"{lesson_id}.json"
        if lesson_file.exists():
            lesson_file.unlink()

        return True

    @property
    def is_loaded(self) -> bool:
        """Check if content is loaded."""
        return self._loaded

    @property
    def lesson_count(self) -> int:
        """Get number of loaded lessons."""
        return len(self._lessons)
