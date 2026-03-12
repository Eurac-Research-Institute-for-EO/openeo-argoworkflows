cwlVersion: v1.2
class: CommandLineTool
baseCommand: python3
arguments: ["-c", "import json; data = {'name': '$(inputs.name)', 'items': list(range($(inputs.count)))}; open('result.json','w').write(json.dumps(data, indent=2))"]
requirements:
  DockerRequirement:
    dockerPull: python:3.11-slim
  InlineJavascriptRequirement: {}
inputs:
  name:
    type: string
  count:
    type: int
outputs:
  result:
    type: File
    outputBinding:
      glob: result.json
