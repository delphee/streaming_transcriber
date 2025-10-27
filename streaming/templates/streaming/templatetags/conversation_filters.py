from django import template

register = template.Library()


@register.filter
def group_segments(segments, pause_threshold=3000):
    """
    Group consecutive segments from the same speaker into paragraphs.
    Creates new paragraph if:
    1. Speaker changes
    2. Pause between segments > pause_threshold milliseconds (default 3000ms = 3 seconds)

    Returns a list of grouped segment dictionaries with:
    - speaker: Speaker object
    - text: Combined text
    - start_time: First segment start
    - end_time: Last segment end
    - confidence: Average confidence
    - segments: List of original segments in this group
    """
    if not segments:
        return []

    grouped = []
    current_group = None

    for segment in segments:
        # Check if we should start a new group
        should_start_new = False

        if current_group is None:
            # First segment
            should_start_new = True
        elif segment.speaker != current_group['speaker']:
            # Different speaker
            should_start_new = True
        elif segment.start_time and current_group['end_time']:
            # Same speaker, check pause duration
            pause_duration = segment.start_time - current_group['end_time']
            if pause_duration > pause_threshold:
                should_start_new = True

        if should_start_new:
            # Save previous group if exists
            if current_group:
                grouped.append(current_group)

            # Start new group
            current_group = {
                'speaker': segment.speaker,
                'text': segment.text,
                'start_time': segment.start_time,
                'end_time': segment.end_time,
                'confidence': segment.confidence if segment.confidence else None,
                'confidence_count': 1 if segment.confidence else 0,
                'confidence_sum': segment.confidence if segment.confidence else 0,
                'segments': [segment],
            }
        else:
            # Append to current group
            current_group['text'] += ' ' + segment.text
            current_group['end_time'] = segment.end_time
            current_group['segments'].append(segment)

            # Update average confidence
            if segment.confidence:
                current_group['confidence_sum'] += segment.confidence
                current_group['confidence_count'] += 1
                current_group['confidence'] = current_group['confidence_sum'] / current_group['confidence_count']

    # Don't forget the last group
    if current_group:
        grouped.append(current_group)

    return grouped


@register.filter
def format_timestamp_ms(milliseconds):
    """Format milliseconds as MM:SS"""
    if milliseconds is None:
        return ""

    seconds = milliseconds / 1000
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


@register.filter
def format_time_range(group):
    """Format time range for a grouped segment"""
    if not group.get('start_time') or not group.get('end_time'):
        return ""

    start = format_timestamp_ms(group['start_time'])
    end = format_timestamp_ms(group['end_time'])

    if start == end:
        return start
    return f"{start} - {end}"