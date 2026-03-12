cwlVersion: v1.2
class: CommandLineTool
baseCommand: sh
arguments: ["-c", "for i in $(seq 1 $(inputs.count)); do echo $(inputs.message); done > output.txt"]
requirements:
  DockerRequirement:
    dockerPull: alpine:3
  InlineJavascriptRequirement: {}
inputs:
  message:
    type: string
  count:
    type: int
outputs:
  output_file:
    type: File
    outputBinding:
      glob: output.txt
