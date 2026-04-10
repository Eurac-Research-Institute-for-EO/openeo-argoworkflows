#!/usr/bin/env cwl-runner
cwlVersion: v1.2
class: CommandLineTool

label: "Echo message to file"
doc: |
  Golden-path CWL CommandLineTool for testing the openEO run_cwl process.
  Writes a message to an output file using stdout capture.

baseCommand: ["sh", "-c"]

requirements:
  DockerRequirement:
    dockerPull: "alpine:3.19"
  ShellCommandRequirement: {}

arguments:
  - valueFrom: "echo $(inputs.message)"
    shellQuote: false

stdout: output.txt

inputs:
  message:
    type: string
    doc: "The message to write to the output file."

outputs:
  output_file:
    type: stdout
    doc: "The file containing the echoed message."
