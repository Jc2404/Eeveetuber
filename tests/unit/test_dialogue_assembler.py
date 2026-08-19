from eeveetuber.dialogue.assembler import IncrementalUtteranceAssembler


def test_emits_complete_sentences_before_finish() -> None:
    assembler = IncrementalUtteranceAssembler()

    assert assembler.push("Hello there") == ()
    first = assembler.push("! How are")
    assert [segment.speakable_text for segment in first] == ["Hello there!"]
    assert assembler.push(" you") == ()
    final = assembler.finish()

    assert [segment.speakable_text for segment in final] == ["How are you"]
    assert [segment.sequence for segment in assembler.segments] == [0, 1]


def test_supports_cjk_sentence_boundaries() -> None:
    assembler = IncrementalUtteranceAssembler()

    segments = assembler.push("你好\uff01今天怎么样\uff1f")

    assert [segment.speakable_text for segment in segments] == [
        "你好\uff01",
        "今天怎么样\uff1f",
    ]


def test_caps_punctuation_free_segment_latency() -> None:
    assembler = IncrementalUtteranceAssembler(max_segment_chars=20)

    segments = assembler.push("one two three four five six")

    assert segments
    assert len(segments[0].speakable_text) <= 20
    assert " ".join(segment.speakable_text for segment in (*segments, *assembler.finish())) == (
        "one two three four five six"
    )
