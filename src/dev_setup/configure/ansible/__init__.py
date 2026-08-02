"""The `devstuff configure ansible` wizard — `ansible.cfg`.

The distinguishing feature of this one is that the tool can tell you what it *read*,
not merely whether your file parsed: `ansible-config dump --only-changed` lists every
setting ansible actually took from the file. That is a stronger check than validation
and it is what catches settings written into a section this ansible does not read.
"""
