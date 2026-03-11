#!/usr/bin/env cwl-runner
cwlVersion: v1.2
class: CommandLineTool

label: "Echo message to file"
doc: |
  Golden-path CWL CommandLineTool for testing the openEO run_cwl process.
  Writes a message to an output file. Uses only standard POSIX tools
  (sh, echo) so it runs in any Docker image including the default CWL
  node container.

baseCommand: ["sh", "-c"]

requirements:
  DockerRequirement:
    dockerPull: "alpine:3.19"

inputs:
  message:
    type: string
    doc: "The message to write to the output file."
    inputBinding:
      position: 1
      valueFrom: |
        echo '$(self)' > output.txt

outputs:
  output_file:
    type: File
    outputBinding:
      glob: "output.txt"
    doc: "The file containing the echoed message."
