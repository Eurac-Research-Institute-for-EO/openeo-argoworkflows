cwlVersion: v1.2
class: CommandLineTool
baseCommand: sh
arguments: ["-c", "echo $(inputs.greeting) $(inputs.name) > output.txt"]
requirements:
  DockerRequirement:
    dockerPull: alpine:3
  InlineJavascriptRequirement: {}
inputs:
  greeting:
    type: string
  name:
    type: string
outputs:
  output_file:
    type: File
    outputBinding:
      glob: output.txt
